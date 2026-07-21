// ══════════════════════════════════════════════════════════════
//  ChatLogRenderer — 把 JSONL 日志渲染成对话
// ══════════════════════════════════════════════════════════════
class ChatLogRenderer {
  constructor(container, isRunning = false, foldProcess = false, projectName = null) {
    this.container = container;   // #log-content div
    this.inner = null;        // .chat-log div
    this.toolMap = {};          // tool_use_id → { headerEl, badgeEl, outputEl, exitEl, toolName, input, wrap }
    this.subagentRenderers = {}; // Task/Agent 工具的 tool_use_id → { renderer, toggleRowEl, revealed }
    this.activeMonitorTasks = {}; // Monitor 工具的 task_id → tool_use_id（system task_started/task_notification 用）
    // TaskCreate/TaskUpdate/TaskList（agent 编排用的任务队列，跟上面注释里派生子agent
    // 的 Task/Agent 工具是两码事）的累加快照：CC 分配的数字 id(string) → {subject, status, activeForm}。
    // 单次 TaskCreate/TaskUpdate 调用只改动其中一条，但每次渲染都展示整份看板的当前状态。
    this.taskBoard = new Map();
    this._buf = '';          // SSE 行缓冲
    this._jsonLineBuffer = ''; // 跨行 JSON 事件缓冲
    this._footerRendered = false;
    this._isPending = false;   // renderPending() 后首次 push() 时清除占位 pill
    this.isRunning = isRunning;   // 是否正在运行
    this.foldProcess = foldProcess; // 是否折叠最新步骤
    this.projectName = projectName; // 所属项目名，用于 CF_SEND 文件下载链接

    // 折叠辅助字段
    this.processWrapper = null;
    this.processBody = null;
    this.toolCount = 0;
    this.idPrefix = Math.random().toString(36).slice(2, 8);
    this.lastBubbleEl = null;   // 保存最后一条 AI 回复节点的引用

    // Hermes plain-text parser state
    this.accountType = '';
    this._hermesState = 'init';   // init | in_response
    this._hermesResponseBuf = [];
    this._hermesDuration = '';
    this._hermesMessages = '';
    this._hermesSessionId = '';

    // Grok streaming-json parser state（支持 thought/text 交替出现）
    this._grokCurrentType    = null;   // 'thought' | 'text' | null —— 当前正在输出的段落类型
    this._grokCurrentBuf     = '';    // 当前段落的已累积文本
    this._grokCurrentEl      = null;  // 当前段落的内容 DOM 元素
    this._grokCurrentTextRef = null;  // {value} 引用，供文本气泡的复制按钮闭包使用
    this._grokRafPending     = false; // requestAnimationFrame 节流标志
    this._grokSegCount       = 0;     // 段落序号，保证元素 ID 唯一
  }

  _ensureProcessWrapper() {
    if (this.processWrapper) return;

    this.processWrapper = document.createElement('div');
    this.processWrapper.className = 'timeline-node intermediate-process-wrapper';

    const bodyClass = this.isRunning ? 'intermediate-process-body' : 'intermediate-process-body collapsed';
    const btnText = this.isRunning ? '收起' : '展开';

    this.processWrapper.innerHTML = `
  <div class="intermediate-process-header">
    <div class="process-title-area">
      <span class="process-icon">⚙️</span>
      <span class="process-title-text">任务执行过程</span>
      <span class="process-stats-badge" id="ps-${this.idPrefix}" style="display:none">0 个步骤</span>
    </div>
    <button class="process-toggle-btn" id="pb-${this.idPrefix}">${btnText}</button>
  </div>
  <div class="${bodyClass}" id="pbody-${this.idPrefix}"></div>
`;

    this.inner.appendChild(this.processWrapper);
    this.processBody = this.processWrapper.querySelector(`#pbody-${this.idPrefix}`);

    const header = this.processWrapper.querySelector('.intermediate-process-header');
    const toggleBtn = this.processWrapper.querySelector(`#pb-${this.idPrefix}`);
    header.addEventListener('click', () => {
      const isCollapsed = this.processBody.classList.toggle('collapsed');
      toggleBtn.textContent = isCollapsed ? '展开' : '收起';
    });
  }

  _appendToProcess(el) {
    this._ensureProcessWrapper();
    this.processBody.appendChild(el);
    this.toolCount++;
    const badge = this.processWrapper.querySelector(`#ps-${this.idPrefix}`);
    if (badge) {
      badge.style.display = '';
      badge.textContent = `${this.toolCount} 个步骤`;
    }
  }

  _appendNode(el) {
    if (this.foldProcess) {
      this._appendToProcess(el);
    } else {
      this.inner.appendChild(el);
    }
  }

  renderPending() {
    this.container.innerHTML = '<div class="chat-log timeline"></div>';
    this.inner = this.container.querySelector('.chat-log');
    const el = document.createElement('div');
    el.className = 'chat-sys-event timeline-node is-muted';
    el.innerHTML = `
      <span class="chat-sys-pill" style="background: rgba(148, 163, 184, 0.08); border-color: rgba(148, 163, 184, 0.22); color: #cbd5e1;">
        <span style="display: inline-block; animation: spin 2s linear infinite; margin-right: 6px;">⏳</span> 任务排队中，等待空闲账号...
      </span>
    `;
    this.inner.appendChild(el);
    this._isPending = true;
  }

  // 账号空闲时的乐观占位：SSE 首条数据到来后自动被 _isPending 机制清除
  renderExecuting() {
    this.container.innerHTML = '<div class="chat-log timeline"></div>';
    this.inner = this.container.querySelector('.chat-log');
    const el = document.createElement('div');
    el.className = 'chat-sys-event timeline-node is-muted';
    el.innerHTML = `
      <span class="chat-sys-pill" style="background: rgba(13,148,136,0.06); border-color: rgba(13,148,136,0.22); color: var(--accent,#0d9488);">
        <span style="display: inline-block; animation: spin 1.2s linear infinite; margin-right: 6px;">⚙️</span> AI 正在执行...
      </span>
    `;
    this.inner.appendChild(el);
    this._isPending = true;
  }

  renderScheduled(executeAt) {
    this.container.innerHTML = '<div class="chat-log timeline"></div>';
    this.inner = this.container.querySelector('.chat-log');
    const displayTime = executeAt ? executeAt.replace('T', ' ') : '';
    const el = document.createElement('div');
    el.className = 'chat-sys-event timeline-node is-muted';
    el.innerHTML = `
      <span class="chat-sys-pill" style="background: rgba(245, 158, 11, 0.08); border-color: rgba(245, 158, 11, 0.22); color: #f59e0b;">
        <span style="margin-right: 6px;">⏰</span> 已定时：将在 ${esc(displayTime)} 自动发送
      </span>
    `;
    this.inner.appendChild(el);
  }

  // ── 全量渲染 ─────────────────────────────────────────────
  render(text, accountType = '') {
    this.accountType = accountType || this.accountType;
    this.container.innerHTML = '<div class="chat-log timeline"></div>';
    this.inner = this.container.querySelector('.chat-log');
    this.toolMap = {};
    this.subagentRenderers = {};
    this.activeMonitorTasks = {};
    this.taskBoard = new Map();
    this._buf = '';
    this._jsonLineBuffer = '';
    this._footerRendered = false;
    this.processWrapper = null;
    this.processBody = null;
    this.toolCount = 0;
    this.lastBubbleEl = null;
    this._hermesState = 'init';
    this._hermesResponseBuf = [];
    this._hermesDuration = '';
    this._hermesMessages = '';
    this._hermesSessionId = '';
    this._grokCurrentType    = null;
    this._grokCurrentBuf     = '';
    this._grokCurrentEl      = null;
    this._grokCurrentTextRef = null;
    this._grokRafPending     = false;
    this._grokSegCount       = 0;

    const lines = text.split('\n');
    let state = 'before';   // before | header | body | footer
    let headerLines = [];
    let bodyLines = [];
    let footerLines = [];

    const nextNonEmptyLine = (start) => {
      for (let i = start; i < lines.length; i++) {
        const candidate = lines[i].trim();
        if (candidate) return candidate;
      }
      return '';
    };
    const isFooterSeparator = (line, idx) => {
      if (!line.startsWith('======')) return false;
      const next = nextNonEmptyLine(idx + 1);
      return next.startsWith('finished:') || next.startsWith('usage status:');
    };

    for (let idx = 0; idx < lines.length; idx++) {
      const raw = lines[idx];
      const line = raw.trimEnd();
      if (state === 'before') {
        if (line.startsWith('=== CoderFleet Task Log ===') || line.startsWith('=== AICM Task Log ===')) { state = 'header'; headerLines = [line]; }
        continue;
      }
      if (state === 'header') {
        if (line.startsWith('======')) { state = 'body'; this._renderHeader(headerLines); continue; }
        headerLines.push(line);
        continue;
      }
      if (state === 'body') {
        if (isFooterSeparator(line, idx)) { this._renderBody(bodyLines); state = 'footer'; continue; }
        bodyLines.push(raw);
        continue;
      }
      if (state === 'footer') {
        if (line.trim()) footerLines.push(line);
      }
    }

    if (state === 'body') this._renderBody(bodyLines);
    if (footerLines.length) this._renderFooter(footerLines.join(' '));
  }

  _renderBody(lines) {
    if (!lines.length) return;
    if (this.accountType === 'hermes' || this.accountType === 'grok' || this.accountType === 'kimi') {
      for (const raw of lines) {
        const line = raw.trim();
        if (line) this._processLine(line);
      }
      this._flushJsonLineBuffer();
      return;
    }
    this._processJsonBodyText(lines.join('\n'));
  }

  _processJsonBodyText(text) {
    let rawLine = '';
    let jsonStart = -1;
    let depth = 0;
    let inString = false;
    let escape = false;

    const flushRawLine = () => {
      const line = rawLine.trim();
      rawLine = '';
      if (line) this._rawLine(line);
    };

    for (let i = 0; i < text.length; i++) {
      const ch = text[i];

      if (jsonStart < 0) {
        if (ch === '{') {
          flushRawLine();
          jsonStart = i;
          depth = 1;
          inString = false;
          escape = false;
        } else if (ch === '\n') {
          flushRawLine();
        } else {
          rawLine += ch;
        }
        continue;
      }

      if (escape) {
        escape = false;
        continue;
      }
      if (ch === '\\') {
        escape = true;
        continue;
      }
      if (ch === '"') {
        inString = !inString;
        continue;
      }
      if (inString) continue;

      if (ch === '{') depth += 1;
      else if (ch === '}') depth -= 1;

      if (depth === 0) {
        const candidate = text.slice(jsonStart, i + 1);
        try {
          this._event(JSON.parse(candidate));
        } catch {
          this._rawLine(candidate);
        }
        jsonStart = -1;
      }
    }

    flushRawLine();
    if (jsonStart >= 0) {
      this._rawLine(text.slice(jsonStart).trim());
    }
  }

  // ── 增量推送（SSE 每行调用一次）─────────────────────────
  push(line) {
    if (!line.trim()) return;

    // 首次收到实际内容时清除 renderPending() 留下的占位 pill。
    // 场景：账号空闲时任务瞬间启动，tail=0 的 SSE 跳过了日志头，直接推后续内容。
    if (this._isPending) {
      this._isPending = false;
      if (this.inner) this.inner.innerHTML = '';
      this.toolMap = {};
      this.subagentRenderers = {};
      this.activeMonitorTasks = {};
      this.taskBoard = new Map();
      this._jsonLineBuffer = '';
      this._footerRendered = false;
      this.processWrapper = null;
      this.processBody = null;
      this.toolCount = 0;
      this.lastBubbleEl = null;
    }

    if (line.startsWith('=== CoderFleet Task Log ===') || line.startsWith('=== AICM Task Log ===')) {
      if (this.inner && this.inner.querySelector('.chat-meta-block')) {
        return;
      }
      if (this.inner) {
        this.inner.innerHTML = '';
      }
      this.toolMap = {};
      this.subagentRenderers = {};
      this.activeMonitorTasks = {};
      this.taskBoard = new Map();
      this._jsonLineBuffer = '';
      this._footerRendered = false;
      this.processWrapper = null;
      this.processBody = null;
      this.toolCount = 0;
      this.lastBubbleEl = null;

      this._isCollectingHeader = true;
      this._headerLines = [line];
      return;
    }

    if (this._isCollectingHeader) {
      if (line.startsWith('======')) {
        this._isCollectingHeader = false;
        this._renderHeader(this._headerLines);
      } else {
        this._headerLines.push(line);
      }
      return;
    }

    // 跳过已渲染的头尾标记（初次全量加载后 SSE 会重复推）
    if (line.startsWith('=== CoderFleet') || line.startsWith('=== AICM') || line.startsWith('======')) return;
    if (line.startsWith('id:') || line.startsWith('account:') ||
      line.startsWith('project:') || line.startsWith('started:') ||
      line.startsWith('prompt:') || line.startsWith('container')) return;
    if (line.startsWith('finished:')) {
      this._flushJsonLineBuffer();
      if (!this._footerRendered) this._renderFooter(line);
      return;
    }
    this._processLine(line.trim());
  }

  // ── 私有：单行处理 ────────────────────────────────────────
  _processLine(line) {
    if (this.accountType === 'hermes') { this._hermesLine(line); return; }
    if (this.accountType === 'grok')   { this._grokLine(line);   return; }
    if (this.accountType === 'kimi')   { this._kimiLine(line);   return; }
    if (!line.startsWith('{') && !this._jsonLineBuffer) { this._rawLine(line); return; }
    this._jsonLineBuffer = this._jsonLineBuffer ? (this._jsonLineBuffer + line) : line;
    let d;
    try { d = JSON.parse(this._jsonLineBuffer); } catch { return; }
    this._jsonLineBuffer = '';
    this._event(d);
  }

  _flushJsonLineBuffer() {
    if (!this._jsonLineBuffer) return;
    const pending = this._jsonLineBuffer;
    this._jsonLineBuffer = '';
    this._rawLine(pending);
  }

  // ── 私有：事件分发 ────────────────────────────────────────
  _event(d) {
    // Claude 的 Task/Agent 子agent事件带 parent_tool_use_id，指向发起它的
    // tool_use.id——这类事件属于子agent自己的会话，不进主时间线，转发给
    // 对应工具卡片内嵌的子会话渲染器（后者是一个完整的 ChatLogRenderer 实例，
    // 复用同一套气泡/工具卡/diff/问答渲染逻辑，天然支持递归嵌套）。
    if (d.parent_tool_use_id) {
      const sub = this.subagentRenderers[d.parent_tool_use_id];
      if (sub) {
        sub.renderer._event(d);
        if (!sub.revealed) { sub.revealed = true; sub.toggleRowEl.style.display = ''; }
        return;
      }
    }
    switch (d.type) {
      // Claude
      case 'system': return this._claudeSys(d);
      case 'assistant': return this._claudeAssistant(d.message);
      case 'user': return this._claudeUser(d.message);
      case 'result': return this._claudeResult(d);
      case 'rate_limit_event': return this._claudeRateLimit(d);
      // Codex
      case 'thread.started': return this._pill('会话开始', d.thread_id ? '#' + String(d.thread_id).slice(0, 8) : '');
      case 'turn.started': return; // 太噪，静默
      case 'turn.ended': return;
      case 'thread.ended': return this._codexEnd(d);
      case 'message': return this._codexMessage(d);
      case 'tool_call': return this._codexToolCall(d);
      case 'tool_result': return this._codexToolResult(d);
      case 'reasoning': return this._thinking(d.text || d.thinking || '');
      case 'item.started': return this._codexItemStarted(d.item);
      case 'item.completed': return this._codexItemCompleted(d.item);
      // OpenCode
      case 'step_start': return this._opencodeStepStart(d);
      case 'tool_use': return this._opencodeToolUse(d);
      case 'text': return this._opencodeText(d);
      case 'step_finish': return this._opencodeStepFinish(d);
      // Pi Agent
      case 'session': return this._piSession(d);
      case 'agent_start': return; // 太噪，静默
      case 'turn_start': return;
      case 'turn_end': return;
      case 'message_start': return;
      case 'message_update': return; // 只在 message_end 渲染完整内容，不逐 delta 渲染
      case 'message_end': return this._piMessageEnd(d);
      case 'tool_execution_start': return this._piToolStart(d);
      case 'tool_execution_update': return; // 太噪，静默；完成时 tool_execution_end 给出完整结果
      case 'tool_execution_end': return this._piToolEnd(d);
      case 'agent_end': return this._piAgentEnd(d);
      case 'agent_settled': return;
      default:
        return this._rawLine(JSON.stringify(d));
    }
  }

  // ── Header 块 ─────────────────────────────────────────────
  _renderHeader(lines) {
    const el = document.createElement('div');
    el.className = 'chat-meta-block';
    el.classList.add('timeline-node', 'is-muted');
    const html = lines
      .filter(l => !l.startsWith('===') && l.includes(':'))
      .map(l => {
        const i = l.indexOf(':');
        const key = l.slice(0, i).trim();
        let val = l.slice(i + 1).trim();
        if (key === 'prompt') {
          val = val.replace(/\\n/g, '\n');
        }
        return `<div class="meta-row"><span class="meta-key">${esc(key)}:</span> <span class="meta-val">${esc(val)}</span></div>`;
      }).join('');
    el.innerHTML = html;
    this._appendNode(el);
  }

  // ── Claude: system ────────────────────────────────────────
  _claudeSys(d) {
    if (d.subtype === 'init') {
      const model = (d.model || '').replace(/^claude-/, '');
      const n = (d.tools || []).length;
      this._pill('Claude 就绪', model + (n ? ` · ${n} 工具` : ''));
      return;
    }
    // Monitor 工具（CC 2.1+ 后台长驻命令）的生命周期用独立的 system 事件驱动，
    // 不经过普通的 tool_result：task_started 登记 task_id→tool_use_id，
    // task_notification 是任务结束的终态信号。这里只做"翻一下这张卡的状态灯"
    // 这一件事——CC 真正的做法是把 task 存活期间被后台任务唤醒的后续轮次都
    // 归并展示到同一个分组里（lobehub 的 activeTasks/pendingExternalSignal 状态机），
    // 那需要一整套跨轮次归因的状态机，和这里"事件流直接转 DOM"的架构不是一个量级，
    // 暂不实现；只做最诚实有用的子集：卡片状态灯准确反映"运行中/已结束"。
    if (d.subtype === 'task_started' && d.task_id && d.tool_use_id) {
      this.activeMonitorTasks[d.task_id] = d.tool_use_id;
      const e = this.toolMap[d.tool_use_id];
      if (e && e.statusEl) {
        e.statusEl.textContent = '⏳';
        e.badgeEl.className = 'tool-badge pending';
      }
      return;
    }
    if (d.subtype === 'task_notification' && d.task_id) {
      const toolUseId = this.activeMonitorTasks[d.task_id];
      delete this.activeMonitorTasks[d.task_id];
      const e = toolUseId && this.toolMap[toolUseId];
      if (e && e.statusEl) {
        e.statusEl.textContent = '✓';
        e.badgeEl.className = 'tool-badge ok';
      }
    }
  }

  // ── Claude: rate_limit_event ───────────────────────────────
  // 每次请求都会带一份 rate_limit_info，status 绝大多数时候是 'allowed'——
  // 正常状态不值得打断时间线；只在真的接近/触发限额时才提示一下。
  _claudeRateLimit(d) {
    const info = d.rate_limit_info || {};
    if (!info.status || info.status === 'allowed') return;
    const label = info.rateLimitType ? `${info.rateLimitType} 限额` : '速率限制';
    this._pill(`⚠ ${label}`, info.status);
  }

  // ── Claude: assistant ─────────────────────────────────────
  _claudeAssistant(msg) {
    if (!msg?.content) return;
    for (const b of msg.content) {
      if (b.type === 'text' && b.text?.trim()) this._bubble(b.text, msg.model);
      else if (b.type === 'thinking' && b.thinking) this._thinking(b.thinking);
      else if (b.type === 'tool_use') this._toolUse(b);
    }
  }

  // ── Claude: user (tool results) ───────────────────────────
  _claudeUser(msg) {
    if (!msg?.content) return;
    for (const b of msg.content) {
      if (b.type === 'tool_result') {
        let text = '';
        let images = [];
        if (typeof b.content === 'string') text = b.content;
        else if (Array.isArray(b.content)) {
          // Read 命中图片文件时，content 里混着一个没有 .text 的 image block——
          // 原先直接 join 会把它拼成空字符串,图片就悄悄消失了。
          text = b.content.filter(c => c.type !== 'image').map(c => c.text || '').join('\n');
          images = b.content.filter(c => c.type === 'image' && c.source);
        }
        this._fillTool(b.tool_use_id, text, b.is_error, images);
      }
    }
  }

  // ── Claude: result ────────────────────────────────────────
  _claudeResult(d) {
    // 1. 如果最终成功且有答复内容，先渲染为 AI 气泡
    if (!d.is_error && d.result && d.result.trim()) {
      this._bubble(d.result);
    }

    // 2. 提取并渲染 Token 用量与费用统计
    const usage = d.usage || {};
    const input = d.input_tokens ?? usage.input_tokens;
    const output = d.output_tokens ?? usage.output_tokens;
    const cacheRead = usage.cache_read_input_tokens;
    const cost = d.cost_usd ?? d.total_cost_usd;
    const turns = d.num_turns;

    const items = [];
    if (input != null) items.push(`输入 <span>${input.toLocaleString()}</span> tok`);
    if (output != null) items.push(`输出 <span>${output.toLocaleString()}</span> tok`);
    if (cacheRead != null && cacheRead > 0) {
      items.push(`缓存读取 <span>${cacheRead.toLocaleString()}</span> tok`);
    }
    if (cost != null) items.push(`费用 <span>$${cost.toFixed(4)}</span>`);
    if (turns != null) items.push(`轮次 <span>${turns}</span>`);

    if (items.length) {
      const el = document.createElement('div');
      el.className = 'chat-usage timeline-node is-muted';
      el.innerHTML = items.map(i => `<div class="usage-item">${i}</div>`).join('');
      this._appendNode(el);
    }

    // 3. 如果是错误，渲染异常阻断横幅
    if (d.is_error && d.result) {
      const el = document.createElement('div');
      el.className = 'chat-sys-event timeline-node is-error';
      el.innerHTML = `<span class="chat-sys-pill" style="background:#2a0e0e; color:#f87171; border-color:#5a1e1e; font-weight:600;">异常阻断 · ${esc(d.result)}</span>`;
      this._appendNode(el);
    }

    // 4. 更新页脚
    const ok = d.subtype === 'success' && !d.is_error;
    if (!this._footerRendered) {
      this._renderFooter(ok ? 'done' : 'failed');
    }
  }

  // ── Codex ─────────────────────────────────────────────────
  _codexMessage(d) {
    if (d.role === 'assistant') {
      const t = typeof d.content === 'string' ? d.content :
        (Array.isArray(d.content) ? d.content.map(b => b.text || '').join('') : '');
      if (t.trim()) this._bubble(t);
    }
  }

  _codexToolCall(d) {
    this._toolUse({
      id: d.id || ('cx-' + Math.random().toString(36).slice(2, 8)),
      name: d.name || 'tool',
      input: d.arguments || d.input || {},
    });
  }

  _codexToolResult(d) {
    const text = typeof d.result === 'string' ? d.result :
      (Array.isArray(d.result) ? d.result.map(c => c.text || '').join('\n') : JSON.stringify(d.result));
    this._fillTool(d.tool_call_id, text, d.is_error);
  }

  _codexEnd(d) {
    if (!this._footerRendered) this._renderFooter(d.result ? 'done' : 'failed');
  }

  // ── Kimi Code ──────────────────────────────────────────────
  _kimiLine(line) {
    if (!line.startsWith('{')) return;
    let d;
    try { d = JSON.parse(line); } catch { this._rawLine(line); return; }

    if (d.role === 'assistant') {
      if (d.content?.trim()) this._bubble(d.content);
      for (const tc of (d.tool_calls || [])) {
        const fn = tc.function || {};
        let input = fn.arguments || {};
        if (typeof input === 'string') {
          try { input = JSON.parse(input); } catch { input = { arguments: input }; }
        }
        this._toolUse({
          id: tc.id || ('kimi-' + Math.random().toString(36).slice(2, 8)),
          name: fn.name || 'tool',
          input,
        });
      }
      return;
    }

    if (d.role === 'tool') {
      this._fillTool(d.tool_call_id, d.content || '', false);
      return;
    }

    if (d.role === 'meta' && d.type === 'session.resume_hint') {
      const sid = d.session_id ? String(d.session_id).slice(0, 12) : '';
      this._pill('Kimi 会话', sid ? `#${sid}` : '');
    }
  }

  _codexItemStarted(item) {
    if (!item) return;
    if (item.type === 'command_execution') {
      const id = item.id;
      const cmd = item.command || '';
      let displayCmd = cmd;
      if (cmd.startsWith('/bin/bash -lc "') && cmd.endsWith('"')) {
        displayCmd = cmd.substring(15, cmd.length - 1).replace(/\\"/g, '"');
      } else if (cmd.startsWith('/bin/bash -lc \'') && cmd.endsWith('\'')) {
        displayCmd = cmd.substring(15, cmd.length - 1);
      }
      this._toolUse({
        id: id,
        name: 'Bash',
        input: { command: displayCmd }
      });
    }
  }

  _codexItemCompleted(item) {
    if (!item) return;
    if (item.type === 'command_execution') {
      const id = item.id;
      if (!this.toolMap[id]) {
        this._codexItemStarted(item);
      }
      const exitCode = item.exit_code;
      const isError = exitCode != null && exitCode !== 0;
      this._fillTool(id, item.aggregated_output || '', isError);
    } else if (item.type === 'agent_message') {
      if (item.text?.trim()) {
        this._bubble(item.text);
      }
    } else if (item.type === 'file_change') {
      const changes = item.changes || [];
      for (const ch of changes) {
        const path = ch.path || '';
        const kind = ch.kind || 'update';

        let opClass = 'edit';
        let opLabel = 'UPDATE';
        if (kind === 'add' || kind === 'create') {
          opClass = 'create';
          opLabel = 'CREATE';
        } else if (kind === 'delete' || kind === 'remove') {
          opClass = 'delete';
          opLabel = 'DELETE';
        }

        const el = document.createElement('div');
        el.className = 'chat-file-card timeline-node';
        let cleanPath = path;
        if (path.startsWith('/workspace/')) {
          cleanPath = path.substring(11);
        }
        el.innerHTML = `
      <span class="file-op-badge ${opClass}">${opLabel}</span>
      <span class="file-path">${esc(cleanPath)}</span>
    `;
        this._appendNode(el);
      }
    }
  }

  // ── Pi Agent ──────────────────────────────────────────────
  _piSession(d) {
    const sid = d.id ? String(d.id).slice(0, 8) : '';
    this._pill('Pi 会话开始', sid ? `#${sid}` : '');
  }

  _piMessageEnd(d) {
    const msg = d.message || {};
    if (msg.role !== 'assistant') return;
    for (const b of (msg.content || [])) {
      if (b.type === 'thinking' && b.thinking) this._thinking(b.thinking);
      else if (b.type === 'text' && b.text?.trim()) this._bubble(b.text, msg.model);
    }
    this._piUsage(msg.usage);
  }

  _piToolStart(d) {
    this._toolUse({
      id: d.toolCallId || ('pi-' + Math.random().toString(36).slice(2, 8)),
      name: d.toolName || 'tool',
      input: d.args || {},
    });
  }

  _piToolEnd(d) {
    const content = d.result?.content;
    const text = Array.isArray(content)
      ? content.map(c => c.text || '').join('\n')
      : (typeof d.result === 'string' ? d.result : '');
    this._fillTool(d.toolCallId, text, !!d.isError);
  }

  _piUsage(usage) {
    if (!usage) return;
    const cost = usage.cost || {};
    const items = [];
    if (usage.input != null) items.push(`输入 <span>${usage.input.toLocaleString()}</span> tok`);
    if (usage.output != null) items.push(`输出 <span>${usage.output.toLocaleString()}</span> tok`);
    if (usage.cacheRead) items.push(`缓存读取 <span>${usage.cacheRead.toLocaleString()}</span> tok`);
    if (usage.cacheWrite) items.push(`缓存写入 <span>${usage.cacheWrite.toLocaleString()}</span> tok`);
    if (cost.total) items.push(`费用 <span>$${Number(cost.total).toFixed(4)}</span>`);

    if (items.length) {
      const el = document.createElement('div');
      el.className = 'chat-usage timeline-node is-muted';
      el.innerHTML = items.map(i => `<div class="usage-item">${i}</div>`).join('');
      this._appendNode(el);
    }
  }

  _piAgentEnd(d) {
    // willRetry === true means pi is about to retry the turn — not actually
    // done yet, a further agent_end will follow. Leave the footer to that one
    // (or to CoderFleet's own wrapper-appended `finished:` line as fallback).
    if (d.willRetry) return;
    if (!this._footerRendered) this._renderFooter('done');
  }

  // ── OpenCode ──────────────────────────────────────────────
  _opencodeStepStart(d) {
    const session = d.sessionID ? '#' + String(d.sessionID).slice(0, 8) : '';
    this._pill('OpenCode 开始', session);
  }

  _opencodeText(d) {
    const part = d.part || {};
    const text = part.text || d.text || '';
    if (!text.trim()) return;

    const thoughtMatch = text.match(/^THOUGHT:\s*([\s\S]*?)\n\nRESPONSE:\s*([\s\S]*)$/);
    if (thoughtMatch) {
      this._thinking(thoughtMatch[1].trim());
      const response = this._cleanOpenCodeResponse(thoughtMatch[2]);
      if (response) this._bubble(response, 'OpenCode');
      return;
    }

    const response = this._cleanOpenCodeResponse(text);
    if (response) this._bubble(response, 'OpenCode');
  }

  _opencodeToolUse(d) {
    const part = d.part || {};
    const state = part.state || {};
    const id = part.callID || part.id || ('oc-' + Math.random().toString(36).slice(2, 8));
    const name = this._opencodeToolName(part.tool || state.tool || 'tool');
    const input = this._normalizeOpenCodeToolInput(name, state.input || {});
    const metadata = state.metadata || {};
    const output = state.output ?? metadata.output ?? '';
    const exitCode = metadata.exit;
    const isComplete = state.status === 'completed' || state.status === 'error' || state.status === 'failed';
    const isError = state.status === 'error' || state.status === 'failed' || (exitCode != null && exitCode !== 0);

    if (!this.toolMap[id]) {
      this._toolUse({ id, name, input });
    }
    if (isComplete) {
      this._fillTool(id, output, isError);
    }
  }

  _opencodeToolName(name) {
    const n = String(name || '').toLowerCase();
    if (n === 'bash' || n === 'shell') return 'Bash';
    if (n === 'write') return 'Write';
    if (n === 'read') return 'Read';
    if (n === 'edit') return 'Edit';
    if (n === 'grep') return 'Grep';
    if (n === 'glob') return 'Glob';
    if (n === 'webfetch' || n === 'web_fetch') return 'WebFetch';
    if (n === 'websearch' || n === 'web_search') return 'WebSearch';
    return name ? String(name) : 'tool';
  }

  _normalizeOpenCodeToolInput(name, input) {
    if (!input) return {};
    const normalized = { ...input };

    if (normalized.file_path == null) {
      normalized.file_path = input.filePath || input.filepath || input.path || '';
    }
    if (normalized.old_string == null && input.oldString != null) {
      normalized.old_string = input.oldString;
    }
    if (normalized.new_string == null && input.newString != null) {
      normalized.new_string = input.newString;
    }
    if (normalized.url == null && input.href != null) {
      normalized.url = input.href;
    }
    if (normalized.query == null && input.search != null) {
      normalized.query = input.search;
    }

    return normalized;
  }

  _cleanOpenCodeResponse(text) {
    return String(text || '')
      .replace(/<step>[\s\S]*?<\/step>/g, '')
      .trim();
  }

  _opencodeStepFinish(d) {
    const part = d.part || {};
    const tokens = part.tokens || {};
    const cache = tokens.cache || {};
    const items = [];
    if (tokens.input != null) items.push(`输入 <span>${tokens.input.toLocaleString()}</span> tok`);
    if (tokens.output != null) items.push(`输出 <span>${tokens.output.toLocaleString()}</span> tok`);
    if (tokens.reasoning != null && tokens.reasoning > 0) items.push(`推理 <span>${tokens.reasoning.toLocaleString()}</span> tok`);
    if (cache.read != null && cache.read > 0) items.push(`缓存读取 <span>${cache.read.toLocaleString()}</span> tok`);
    if (cache.write != null && cache.write > 0) items.push(`缓存写入 <span>${cache.write.toLocaleString()}</span> tok`);
    if (tokens.total != null) items.push(`总计 <span>${tokens.total.toLocaleString()}</span> tok`);
    if (part.cost != null) items.push(`费用 <span>$${Number(part.cost).toFixed(4)}</span>`);

    if (items.length) {
      const el = document.createElement('div');
      el.className = 'chat-usage timeline-node is-muted';
      el.innerHTML = items.map(i => `<div class="usage-item">${i}</div>`).join('');
      this._appendNode(el);
    }

    if (!this._footerRendered) {
      this._renderFooter(part.reason === 'error' ? 'failed' : 'done');
    }
  }

  // ── 文件下载标记检测：<!-- CF_SEND: path --> ─────────────
  _injectDownloadCards(text) {
    if (!this.projectName) return text;
    return text.replace(/<!--\s*CF_SEND:\s*([^>]+?)\s*-->/g, (_, rawPath) => {
      const filePath = rawPath.trim();
      const filename = filePath.split('/').pop() || filePath;
      const rawUrl = `/api/projects/${encodeURIComponent(this.projectName)}/download?path=${encodeURIComponent(filePath)}`;
      const url = typeof authedUrl === 'function' ? authedUrl(rawUrl) : rawUrl;
      return (
        `\n<div class="cf-download-card">` +
          `<div class="cf-dl-icon">` +
            `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">` +
              `<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14,2 14,8 20,8"/><line x1="12" y1="18" x2="12" y2="12"/><polyline points="9,15 12,18 15,15"/>` +
            `</svg>` +
          `</div>` +
          `<div class="cf-dl-info">` +
            `<span class="cf-dl-name">${esc(filename)}</span>` +
            `<span class="cf-dl-path">${esc(filePath)}</span>` +
          `</div>` +
          `<a class="cf-dl-btn" href="${esc(url)}" download="${esc(filename)}">下载</a>` +
        `</div>\n`
      );
    });
  }

  // ── AI 文字气泡 ───────────────────────────────────────────
  _bubble(text, model) {
    const label = model
      ? model.replace(/^claude-/, '').split('-').slice(0, 2).join('-')
      : 'AI';
    const displayText = this._injectDownloadCards(text);
    const showTranslate = isMostlyNonChinese(text);
    const el = document.createElement('div');
    el.className = 'chat-bubble-wrap ai-reply';
    el.classList.add('timeline-node');
    el.innerHTML = `
  <div class="bubble-body">
    <div class="bubble">
      <div class="bubble-label-row">
        <div class="bubble-label">${esc(label)}</div>
        <div class="bubble-actions">
          ${showTranslate ? `<button class="bubble-translate-btn" title="翻译">${translateBtnSVG()}</button>` : ''}
          <button class="bubble-copy-btn" title="复制">${copyBtnSVG()}</button>
        </div>
      </div>
      <div class="bubble-content">${renderMd(displayText)}</div>
    </div>
  </div>`;

    el.querySelector('.bubble-copy-btn').addEventListener('click', () => {
      copyTextToClipboard(text, el.querySelector('.bubble-copy-btn'));
    });

    const tbtn = el.querySelector('.bubble-translate-btn');
    if (tbtn) {
      tbtn.addEventListener('click', () => {
        toggleBubbleTranslation(tbtn, el.querySelector('.bubble-content'), text);
      });
    }

    // 如果之前有渲染过 AI 回复，说明之前的回复并非最后一条，需要移入折叠包中
    if (this.lastBubbleEl) {
      if (this.foldProcess) {
        this._appendToProcess(this.lastBubbleEl);
      } else {
        this.inner.appendChild(this.lastBubbleEl);
      }
    }

    this.inner.appendChild(el);
    this.lastBubbleEl = el;
  }

  // ── 思考气泡 ──────────────────────────────────────────────
  _thinking(text) {
    if (!text?.trim()) return;
    const el = document.createElement('div');
    el.className = 'chat-bubble-wrap thought-reply';
    el.classList.add('timeline-node', 'is-muted');
    el.innerHTML = `
  <div class="bubble-body">
    <div class="bubble-label think-label">思考过程</div>
    <div class="bubble think-bubble">${renderMd(text)}</div>
  </div>`;
    this._appendNode(el);
  }

  // ── 工具调用卡片 ──────────────────────────────────────────
  _toolUse(block) {
    const { id, name, input } = block;
    if (name === 'AskUserQuestion') { this._toolUseAskUserQuestion(block); return; }
    if (name === 'TodoWrite') { this._toolUseTodoWrite(block); return; }
    if (name === 'TaskUpdate') { this._toolUseTaskUpdate(block); return; }
    if (name === 'TaskList') { this._toolUseTaskList(block); return; }
    const summary = formatToolSummary(name, input);
    const icon = toolIcon(name);
    // Edit 工具：old_string/new_string 用逐行 diff 展示，比截断后的纯文本预览
    // 更能一眼看出改了什么;其余工具沿用原来的 JSON/命令预览。
    const isDiff = name === 'Edit' && input && (input.old_string != null || input.new_string != null);
    const detail = isDiff ? '' : formatToolInput(name, input);
    const hasInput = isDiff || (detail && detail !== summary);

    const wrap = document.createElement('div');
    wrap.className = 'chat-tool-wrap';
    wrap.classList.add('timeline-node');
    wrap.dataset.toolId = id;

    const inputBody = isDiff
      ? `<div class="tool-input tool-diff">${renderInlineDiff(input.old_string, input.new_string)}</div>`
      : (hasInput ? `<div class="tool-input">${esc(detail)}</div>` : '');
    // 折叠区里输入/输出紧挨着堆叠，展开后不加标签容易分不清哪段是哪段——
    // 尤其是 Bash 类工具，命令预览和执行结果长得都差不多。
    const inputHtml = inputBody
      ? `<div class="tool-section-label">${isDiff ? '改动' : '输入'}</div>${inputBody}`
      : '';

    // Task/Agent：这是 CC 派生子agent的工具，子agent自己的 assistant/user 轮次会
    // 带 parent_tool_use_id 指回这里，用一个嵌套的 ChatLogRenderer 承接（见 _event）。
    // 面板默认隐藏，第一条子agent事件到达时才现身——不是每次 Task 调用都真的分叉出
    // 独立会话，没内容的话不该有一个空面板杵在那儿。
    const isSubagentSpawn = name === 'Task' || name === 'Agent';
    const subagentHtml = isSubagentSpawn ? `
    <div class="subagent-toggle-row" id="sar-${id}" style="display:none">
      <button class="subagent-toggle-btn" id="sabtn-${id}">▸ 子agent 会话</button>
    </div>
    <div class="subagent-thread collapsed" id="sathread-${id}"><div class="chat-log timeline"></div></div>` : '';

    // ExitWorktree 且 action:'remove' + discard_changes:true 是真正的破坏性操作
    // （移除 worktree 且不保留改动），单独标一个红色警示徽章，不要淹没在其余
    // 工具卡片同样的视觉权重里。
    const isDiscardRisk = name === 'ExitWorktree' && input?.action === 'remove' && input?.discard_changes === true;
    const riskBadgeHtml = isDiscardRisk ? `<span class="tool-risk-badge">⚠ 丢弃改动</span>` : '';

    const card = document.createElement('div');
    card.className = 'chat-tool-card';
    card.innerHTML = `
  <div class="chat-tool-header">
    <span class="tool-status" id="ts-${id}">⏳</span>
    <span class="tool-badge pending" id="tb-${id}">${esc(icon)} ${esc(name)}</span>
    <span class="tool-cmd" title="${esc(summary)}">${esc(summary)}</span>
    ${riskBadgeHtml}
    <button class="tool-toggle" id="tt-${id}">展开</button>
  </div>
  <div class="tool-body collapsed" id="tbody-${id}">
    ${inputHtml}
    <div class="tool-section-label" id="tol-${id}" style="display:none">输出</div>
    <div class="tool-output" id="to-${id}" style="display:none"></div>
    <div class="tool-exit"   id="te-${id}" style="display:none"></div>
    ${subagentHtml}
  </div>`;

    wrap.appendChild(card);
    this._appendNode(wrap);

    // Toggle 逻辑
    const header = card.querySelector('.chat-tool-header');
    const body = document.getElementById(`tbody-${id}`);
    const btn = document.getElementById(`tt-${id}`);
    header.addEventListener('click', () => {
      const collapsed = body.classList.toggle('collapsed');
      btn.textContent = collapsed ? '展开' : '收起';
    });
    btn.addEventListener('click', e => { e.stopPropagation(); header.click(); });

    if (isSubagentSpawn) {
      const toggleRowEl = document.getElementById(`sar-${id}`);
      const subBtn = document.getElementById(`sabtn-${id}`);
      const subThread = document.getElementById(`sathread-${id}`);
      subBtn.addEventListener('click', e => {
        e.stopPropagation();
        const collapsed = subThread.classList.toggle('collapsed');
        subBtn.textContent = collapsed ? '▸ 子agent 会话' : '▾ 子agent 会话';
      });
      const subRenderer = new ChatLogRenderer(subThread, this.isRunning, false, this.projectName);
      subRenderer.inner = subThread.querySelector('.chat-log');
      this.subagentRenderers[id] = { renderer: subRenderer, toggleRowEl, revealed: false };
    }

    this.toolMap[id] = {
      statusEl: document.getElementById(`ts-${id}`),
      badgeEl: document.getElementById(`tb-${id}`),
      outputEl: document.getElementById(`to-${id}`),
      outputLabelEl: document.getElementById(`tol-${id}`),
      exitEl: document.getElementById(`te-${id}`),
      bodyEl: body,
      btnEl: btn,
      wrap, name, input,
    };
  }

  // ── AskUserQuestion：问答卡片 ──────────────────────────────
  // 交互式提问工具，input 是 1-4 道题（question/header/options/multiSelect）；
  // tool_result 到达前先渲染"等待作答"占位，到达后按题目匹配已选项打勾，
  // 匹配不上（纯文本/自由回复）就整卡底部加一行"回复"，而不是硬套成 JSON 转储。
  _toolUseAskUserQuestion(block) {
    const { id, name, input } = block;
    const questions = normalizeAskUserQuestions(input);
    const multiple = questions.length > 1;
    const icon = toolIcon(name);
    const summary = questions.length === 1 ? questions[0].question : `${questions.length} 个问题`;

    const wrap = document.createElement('div');
    wrap.className = 'chat-tool-wrap';
    wrap.classList.add('timeline-node');
    wrap.dataset.toolId = id;

    const qBlocks = questions.map((q, i) => `
  <div class="askq-block">
    ${multiple ? `<span class="askq-ordinal">Q${i + 1}</span>` : ''}
    <div class="askq-body">
      <div class="askq-title-row">
        <span class="askq-question">${esc(q.question)}</span>
        ${multiple && q.header ? `<span class="askq-header-chip">${esc(q.header)}</span>` : ''}
      </div>
      <div class="askq-answer" id="askq-ans-${id}-${i}"><span class="askq-unanswered">等待作答…</span></div>
    </div>
  </div>`).join('');

    const card = document.createElement('div');
    card.className = 'chat-tool-card';
    card.innerHTML = `
  <div class="chat-tool-header">
    <span class="tool-status" id="ts-${id}">⏳</span>
    <span class="tool-badge pending" id="tb-${id}">${esc(icon)} ${esc(name)}</span>
    <span class="tool-cmd" title="${esc(summary)}">${esc(summary)}</span>
    <button class="tool-toggle" id="tt-${id}">收起</button>
  </div>
  <div class="tool-body" id="tbody-${id}">
    <div class="tool-input tool-askq" id="tiq-${id}">${qBlocks}</div>
    <div class="tool-exit" id="te-${id}" style="display:none"></div>
  </div>`;

    wrap.appendChild(card);
    this._appendNode(wrap);

    const header = card.querySelector('.chat-tool-header');
    const body = document.getElementById(`tbody-${id}`);
    const btn = document.getElementById(`tt-${id}`);
    header.addEventListener('click', () => {
      const collapsed = body.classList.toggle('collapsed');
      btn.textContent = collapsed ? '展开' : '收起';
    });
    btn.addEventListener('click', e => { e.stopPropagation(); header.click(); });

    this.toolMap[id] = {
      statusEl: document.getElementById(`ts-${id}`),
      badgeEl: document.getElementById(`tb-${id}`),
      outputEl: null,
      outputLabelEl: null,
      exitEl: document.getElementById(`te-${id}`),
      bodyEl: body,
      btnEl: btn,
      wrap, name, input,
      askQuestions: questions,
    };
  }

  _fillAskUserAnswers(id, e, text) {
    const questions = e.askQuestions || [];
    const raw = (text || '').trim();
    let answers = null;

    if (raw) {
      try {
        const parsed = JSON.parse(raw);
        const src = (parsed && typeof parsed === 'object' && !Array.isArray(parsed) && parsed.answers
          && typeof parsed.answers === 'object') ? parsed.answers : parsed;
        if (src && typeof src === 'object' && !Array.isArray(src)) answers = src;
      } catch { /* 非 JSON：走自由回复分支 */ }
    }

    const matchedAny = !!answers && questions.some(
      q => Object.prototype.hasOwnProperty.call(answers, q.question)
    );

    questions.forEach((q, i) => {
      const slot = document.getElementById(`askq-ans-${id}-${i}`);
      if (!slot) return;
      if (matchedAny) {
        const val = answers[q.question];
        const labels = Array.isArray(val) ? val : (val != null ? [val] : []);
        slot.innerHTML = labels.length
          ? labels.map(label => {
            const opt = (q.options || []).find(o => o.label === label);
            const desc = (opt && opt.description && opt.description !== label)
              ? `<div class="askq-desc">${esc(opt.description)}</div>` : '';
            return `<div class="askq-answer-line"><span class="askq-check">✓</span><div><div class="askq-answer-text">${esc(label)}</div>${desc}</div></div>`;
          }).join('')
          : `<span class="askq-unanswered">未作答</span>`;
      } else if (!raw) {
        slot.innerHTML = `<span class="askq-unanswered">未作答</span>`;
      } else {
        slot.innerHTML = '';
      }
    });

    if (!matchedAny && raw) {
      const wrap = document.getElementById(`tiq-${id}`);
      if (wrap) {
        const fr = document.createElement('div');
        fr.className = 'askq-freeform';
        fr.innerHTML = `<span class="askq-check">✎</span><span class="askq-answer-text">${esc(raw)}</span>`;
        wrap.appendChild(fr);
      }
    }
  }

  // 待办/任务面板的单行渲染，TodoWrite 和下面的 TaskCreate/Update/List 任务看板共用——
  // 两者的条目形状不同（{content,status,activeForm} vs {subject,status,activeForm}），
  // 调用方各自把"非 in_progress 状态下要显示的文字"传进 text 参数。
  _renderTaskRowHtml(text, status, activeForm) {
    if (status === 'in_progress') {
      return `<div class="todo-row todo-row-active"><span class="todo-row-icon">▶</span><span class="todo-row-text todo-active">${esc(activeForm || text || '')}</span></div>`;
    }
    if (status === 'deleted') {
      return `<div class="todo-row"><span class="todo-check">✗</span><span class="todo-row-text todo-done">${esc(text || '')}</span></div>`;
    }
    const done = status === 'completed';
    return `<div class="todo-row"><span class="todo-check${done ? ' done' : ''}">${done ? '✓' : '○'}</span><span class="todo-row-text${done ? ' todo-done' : ''}">${esc(text || '')}</span></div>`;
  }

  // ── TodoWrite：待办清单卡片 ────────────────────────────────
  // input.todos = [{content, status, activeForm}]，status 是 pending/in_progress/completed。
  // tool_result 只是一句确认文本（"Todos have been modified successfully"之类），没有信息量，
  // 卡片本身就是从 tool_use.input 里的最新清单状态渲染完的，不需要等 tool_result 再填充。
  _toolUseTodoWrite(block) {
    const { id, name, input } = block;
    const todos = Array.isArray(input?.todos) ? input.todos : [];
    const total = todos.length;
    const completed = todos.filter(t => t?.status === 'completed').length;
    const inProgress = todos.find(t => t?.status === 'in_progress');
    const allDone = total > 0 && completed === total;

    const icon = toolIcon(name);
    const summary = inProgress
      ? (inProgress.activeForm || inProgress.content || '')
      : (allDone ? '全部完成' : `${total} 项待办`);

    const headerIcon = inProgress ? '▶' : (allDone ? '✓' : '☰');
    const headerLabel = inProgress ? '当前步骤' : (allDone ? '全部完成' : '待办事项');
    const headerDetailText = inProgress ? (inProgress.activeForm || inProgress.content || '') : '';

    const rows = todos.map(t => this._renderTaskRowHtml(t?.content, t?.status, t?.activeForm)).join('');

    const wrap = document.createElement('div');
    wrap.className = 'chat-tool-wrap';
    wrap.classList.add('timeline-node');
    wrap.dataset.toolId = id;

    const card = document.createElement('div');
    card.className = 'chat-tool-card';
    card.innerHTML = `
  <div class="chat-tool-header">
    <span class="tool-status" id="ts-${id}">⏳</span>
    <span class="tool-badge pending" id="tb-${id}">${esc(icon)} ${esc(name)}</span>
    <span class="tool-cmd" title="${esc(summary)}">${esc(summary)}</span>
    <button class="tool-toggle" id="tt-${id}">收起</button>
  </div>
  <div class="tool-body" id="tbody-${id}">
    <div class="todo-header">
      <span class="todo-header-icon">${headerIcon}</span>
      <span class="todo-header-label">${esc(headerLabel)}${headerDetailText ? `: <span class="todo-header-detail">${esc(headerDetailText)}</span>` : ''}</span>
      <span class="todo-header-count">${completed}/${total}</span>
    </div>
    <div class="todo-list">${rows}</div>
  </div>`;

    wrap.appendChild(card);
    this._appendNode(wrap);

    const header = card.querySelector('.chat-tool-header');
    const body = document.getElementById(`tbody-${id}`);
    const btn = document.getElementById(`tt-${id}`);
    header.addEventListener('click', () => {
      const collapsed = body.classList.toggle('collapsed');
      btn.textContent = collapsed ? '展开' : '收起';
    });
    btn.addEventListener('click', e => { e.stopPropagation(); header.click(); });

    this.toolMap[id] = {
      statusEl: document.getElementById(`ts-${id}`),
      badgeEl: document.getElementById(`tb-${id}`),
      outputEl: null,
      outputLabelEl: null,
      exitEl: null,
      bodyEl: body,
      btnEl: btn,
      wrap, name, input,
      isTodoWrite: true,
    };
  }

  // ── TaskUpdate：更新任务看板并展示这一次更新做了什么 ─────────
  // input = {taskId, status?, subject?, activeForm?, ...}。status:'deleted' 直接从
  // this.taskBoard 里摘掉，其余状态合并进已有条目（没有就新建一条只有 id 的占位）。
  // 卡片头部显示"这一次更新"本身（已完成/已开始/已重置/已删除 + 主题），body 里展示
  // 整份看板此刻的最新状态——不是只显示这一条，这样连续几次 TaskUpdate 才能看出进度。
  _toolUseTaskUpdate(block) {
    const { input } = block;
    const taskId = input?.taskId != null ? String(input.taskId) : null;
    let subjectForHeader = input?.subject || '';

    if (taskId) {
      const existing = this.taskBoard.get(taskId) || {};
      if (!subjectForHeader) subjectForHeader = existing.subject || '';
      if (input.status === 'deleted') {
        this.taskBoard.delete(taskId);
      } else {
        const next = { ...existing };
        if (input.subject != null) next.subject = input.subject;
        if (input.activeForm != null) next.activeForm = input.activeForm;
        if (input.status != null) next.status = input.status;
        this.taskBoard.set(taskId, next);
      }
    }

    const STATUS_META = {
      completed:   { icon: '✓', verb: '已完成' },
      deleted:     { icon: '✗', verb: '已删除' },
      in_progress: { icon: '▶', verb: '已开始' },
      pending:     { icon: '↺', verb: '已重置' },
    };
    const meta = STATUS_META[input?.status] || { icon: '☰', verb: '更新任务' };

    this._renderTaskBoardCard(block, { icon: meta.icon, label: meta.verb, detail: subjectForHeader });
  }

  // ── TaskList：不改动看板状态，只是把当前快照重新展示一遍 ───────
  _toolUseTaskList(block) {
    this._renderTaskBoardCard(block, null);
  }

  // TaskUpdate/TaskList 共用的任务看板卡片。headerOverride 为 null 时展示标准聚合头
  // （当前步骤 / 全部完成 / 任务看板 + 完成数/总数）；TaskUpdate 传入
  // {icon, label, detail} 描述"这一次更新做了什么"，覆盖掉聚合头。
  _renderTaskBoardCard(block, headerOverride) {
    const { id, name, input } = block;
    const items = [...this.taskBoard.entries()]
      .sort((a, b) => Number(a[0]) - Number(b[0]))
      .map(([taskId, t]) => ({ id: taskId, ...t }));

    const total = items.length;
    const completed = items.filter(t => t.status === 'completed').length;
    const inProgress = items.find(t => t.status === 'in_progress');
    const allDone = total > 0 && completed === total;

    const icon = toolIcon(name);
    const headerIcon = headerOverride ? headerOverride.icon : (inProgress ? '▶' : (allDone ? '✓' : '☰'));
    const headerLabel = headerOverride ? headerOverride.label : (inProgress ? '当前步骤' : (allDone ? '全部完成' : '任务看板'));
    const headerDetailText = headerOverride
      ? (headerOverride.detail || '')
      : (inProgress ? (inProgress.activeForm || inProgress.subject || '') : '');
    const summary = headerDetailText || headerLabel;

    const rows = items.length
      ? items.map(t => this._renderTaskRowHtml(t.subject, t.status, t.activeForm)).join('')
      : `<div class="todo-row"><span class="todo-row-text" style="opacity:.6">(暂无任务)</span></div>`;

    const wrap = document.createElement('div');
    wrap.className = 'chat-tool-wrap';
    wrap.classList.add('timeline-node');
    wrap.dataset.toolId = id;

    const card = document.createElement('div');
    card.className = 'chat-tool-card';
    card.innerHTML = `
  <div class="chat-tool-header">
    <span class="tool-status" id="ts-${id}">⏳</span>
    <span class="tool-badge pending" id="tb-${id}">${esc(icon)} ${esc(name)}</span>
    <span class="tool-cmd" title="${esc(summary)}">${esc(summary)}</span>
    <button class="tool-toggle" id="tt-${id}">收起</button>
  </div>
  <div class="tool-body" id="tbody-${id}">
    <div class="todo-header">
      <span class="todo-header-icon">${headerIcon}</span>
      <span class="todo-header-label">${esc(headerLabel)}${headerDetailText ? `: <span class="todo-header-detail">${esc(headerDetailText)}</span>` : ''}</span>
      <span class="todo-header-count">${completed}/${total}</span>
    </div>
    <div class="todo-list">${rows}</div>
  </div>`;

    wrap.appendChild(card);
    this._appendNode(wrap);

    const header = card.querySelector('.chat-tool-header');
    const body = document.getElementById(`tbody-${id}`);
    const btn = document.getElementById(`tt-${id}`);
    header.addEventListener('click', () => {
      const collapsed = body.classList.toggle('collapsed');
      btn.textContent = collapsed ? '展开' : '收起';
    });
    btn.addEventListener('click', e => { e.stopPropagation(); header.click(); });

    this.toolMap[id] = {
      statusEl: document.getElementById(`ts-${id}`),
      badgeEl: document.getElementById(`tb-${id}`),
      outputEl: null,
      outputLabelEl: null,
      exitEl: null,
      bodyEl: body,
      btnEl: btn,
      wrap, name, input,
      isTaskBoard: true,
    };
  }

  // 单条工具输出直接塞进 textContent 的字符上限：少数工具（Bash 里 cat 大文件、Read 整份
  // 长文件等）会把几十上百 KB 原样写进 tool_result，历史回放时这些内容大多数还在折叠区里
  // 没人看，却仍然要整体构建成 DOM——手机端一次打开会话要拼好几个这样的任务，主线程明显卡顿。
  // 超限后先截断显示，点「显示完整输出」再补上剩余部分。
  static TOOL_OUTPUT_TRUNCATE_LEN = 8000;

  // 这些工具的结果几乎总是"要看的东西"（改了什么/建了什么/计划是什么），
  // 不管长短都直接展开，不再套用行数/字符数的通用启发式。
  // TodoWrite/AskUserQuestion 不在这张表里——它们各自走专门的渲染方法
  // （_toolUseTodoWrite/_toolUseAskUserQuestion），从不经过这条通用展开逻辑。
  static ALWAYS_EXPAND_TOOLS = new Set(['Edit', 'Write', 'Task']);

  // Read 命中图片文件时 tool_result 带的是 base64 image block 而非文本，
  // 原来直接当文本处理会渲染出一片空白——这里换成缩略图，点击可在新标签打开原图。
  _setToolOutputImages(el, images) {
    el.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.className = 'tool-output-images';
    for (const img of images) {
      const src = img.source || {};
      let url = '';
      if (src.type === 'base64' && src.data) url = `data:${src.media_type || 'image/png'};base64,${src.data}`;
      else if (src.type === 'url' && src.url) url = src.url;
      if (!url) continue;
      const el2 = document.createElement('img');
      el2.className = 'tool-output-img';
      el2.src = url;
      el2.loading = 'lazy';
      el2.addEventListener('click', ev => { ev.stopPropagation(); window.open(url, '_blank'); });
      wrap.appendChild(el2);
    }
    el.appendChild(wrap);
  }

  // ansiToHtml（utils.js）把 Bash 类工具输出里的 ANSI 颜色码渲染成带色 <span>；
  // 没有转义码的普通文本原样走 esc() 转义，跟原来 textContent 视觉上没有差别，
  // 所以这里对所有工具的输出统一用它，不需要按工具名区分。
  _setToolOutputText(el, out, ok) {
    if (!out) { el.textContent = ok ? '(无输出)' : '(执行失败)'; return; }
    if (out.length <= ChatLogRenderer.TOOL_OUTPUT_TRUNCATE_LEN) { el.innerHTML = ansiToHtml(out); return; }

    el.innerHTML = ansiToHtml(out.slice(0, ChatLogRenderer.TOOL_OUTPUT_TRUNCATE_LEN));
    const remaining = out.length - ChatLogRenderer.TOOL_OUTPUT_TRUNCATE_LEN;
    const more = document.createElement('button');
    more.className = 'tool-output-more';
    more.textContent = `… 显示完整输出（还有 ${remaining.toLocaleString()} 字符）`;
    more.addEventListener('click', ev => {
      ev.stopPropagation();
      el.innerHTML = ansiToHtml(out);
      more.remove();
    });
    el.after(more);
  }

  // ── 填充工具结果 ──────────────────────────────────────────
  _fillTool(id, text, isError, images) {
    const e = this.toolMap[id];
    if (!e) return;

    const ok = !isError;
    e.statusEl.textContent = ok ? '✓' : '✗';
    e.badgeEl.className = `tool-badge ${ok ? 'ok' : 'fail'}`;
    e.badgeEl.textContent = `${toolIcon(e.name)} ${e.name}`;

    if (e.name === 'AskUserQuestion' && e.askQuestions) {
      this._fillAskUserAnswers(id, e, text);
      e.exitEl.style.display = '';
      e.exitEl.className = `tool-exit ${ok ? 'ok-exit' : 'fail-exit'}`;
      e.exitEl.textContent = ok ? '✓  已作答' : '✗  未作答';
      return;
    }

    // TodoWrite/TaskUpdate/TaskList 的卡片已经从 tool_use.input / this.taskBoard 渲染
    // 完整——tool_result 只是一句确认文本，没有 outputEl/exitEl 可填
    // （两者的 toolMap 条目里都是 null），到这就结束。
    if (e.isTodoWrite || e.isTaskBoard) return;

    // TaskCreate 的 tool_result 是 CC 自己确认成功创建的文本："Task #<N> created
    // successfully: <subject>"——数字 id 只有这时候才揭晓（tool_use.input 里没有，
    // 是 CC 分配的），要靠正则从这句话里抠出来才能把它登记进任务看板供后续
    // TaskUpdate/TaskList 引用。TaskCreate 本身仍然走通用卡片正常展示这行输出，
    // 不提前 return——这里只是顺带把状态记下来。
    if (e.name === 'TaskCreate' && ok) {
      const m = /Task #(\d+) created successfully/.exec(text || '');
      if (m) {
        this.taskBoard.set(m[1], {
          subject: e.input?.subject || '',
          activeForm: e.input?.activeForm || '',
          status: 'pending',
        });
      }
    }

    // Read 工具的文本结果是 CLI 自带的 `cat -n` 式行号（"␣␣␣1\t内容"），对着行号
    // 读代码没有意义，展示前统一剥掉。
    const rawOut = e.name === 'Read' ? stripReadLineNumbers(text || '') : (text || '');
    const out = rawOut.trim();
    const hasImages = !!(images && images.length);
    if (e.outputLabelEl) e.outputLabelEl.style.display = '';
    e.outputEl.style.display = '';
    e.outputEl.className = `tool-output${ok ? '' : ' is-error'}`;
    if (hasImages) {
      this._setToolOutputImages(e.outputEl, images);
    } else if (e.name === 'Skill' && out) {
      // Skill 工具的产出本来就是给人看的说明文字，通常带 markdown 格式
      // （列表/代码块/加粗），按 markdown 渲染比转义成一大段纯文本可读得多。
      e.outputEl.classList.add('tool-output-md');
      e.outputEl.innerHTML = renderMd(out);
    } else {
      this._setToolOutputText(e.outputEl, out, ok);
    }

    e.exitEl.style.display = '';
    e.exitEl.className = `tool-exit ${ok ? 'ok-exit' : 'fail-exit'}`;
    e.exitEl.textContent = ok ? '✓  exit 0' : '✗  error';

    // 自动展开：要么工具本身总是值得一看，要么命中了图片，要么输出足够短
    const lineCount = out.split('\n').length;
    const shouldExpand = ChatLogRenderer.ALWAYS_EXPAND_TOOLS.has(e.name) ||
      (e.name === 'Read' && hasImages) ||
      (lineCount <= 6 && out.length <= 400);
    if (shouldExpand) {
      e.bodyEl.classList.remove('collapsed');
      e.btnEl.textContent = '收起';
    }

    // 文件操作：追加变更徽章。Write/Edit 的卡片头部本来就显示着同一个 file_path
    // （Edit 现在还带完整 diff 正文），这里再补一张"EDIT xxx.js"卡纯属重复。
    // NotebookEdit 没有专门的 summary/input 格式化（走的是默认 JSON 预览），
    // 头部看不出干净的路径，这里的徽章是它唯一的清晰路径展示，所以保留。
    if (e.name === 'NotebookEdit' && ok) {
      // NotebookEdit 的真实字段是 notebook_path，不是 file_path/path——之前这行
      // 一直取不到值，徽章从未真正显示过。
      const fp = e.input?.notebook_path || e.input?.file_path || e.input?.path || '';
      if (fp) {
        const fc = document.createElement('div');
        fc.className = 'chat-file-card timeline-node';
        fc.innerHTML = `<span class="file-op-badge edit">EDIT</span><span class="file-path">${esc(fp)}</span>`;
        e.wrap.after(fc);
      }
    }
  }

  // ── Footer ────────────────────────────────────────────────
  _renderFooter(text) {
    if (this._footerRendered) return;
    this._footerRendered = true;
    let status = 'done', label = '✓ 任务完成';
    if (typeof text === 'string') {
      if (text.includes('killed')) { status = 'killed'; label = '⊘ 任务已终止'; }
      else if (text.includes('failed') || text.includes('error')) {
        status = 'failed';
        const m = text.match(/\[([^\]]+)\]/);
        label = '✗ 任务失败' + (m ? '  · ' + m[1] : '');
      }
    }
    const el = document.createElement('div');
    el.className = 'chat-footer';
    el.classList.add('timeline-node', status === 'failed' ? 'is-error' : 'is-muted');
    el.innerHTML = `<span class="footer-pill ${status}">${esc(label)}</span>`;
    this._appendNode(el);
  }

  // ── 系统药丸 ──────────────────────────────────────────────
  _pill(label, detail) {
    const el = document.createElement('div');
    el.className = 'chat-sys-event';
    el.classList.add('timeline-node', 'is-muted');
    el.innerHTML = `<span class="chat-sys-pill">${esc(label)}${detail ? ` <span class="pill-detail">· ${esc(detail)}</span>` : ''}</span>`;
    this._appendNode(el);
  }

  // ── Grok streaming-json 解析器 ────────────────────────────
  //
  // 每行格式之一：
  //   {"type":"thought","data":"<token>"}   → 思考过程（流式拼接）
  //   {"type":"text","data":"<token>"}      → 回复正文（流式拼接）
  //   {"type":"end","stopReason":"...","sessionId":"...","requestId":"..."}
  //
  // 非 JSON 行（如 printf 写入的 grok_session_id=... 标记行）静默忽略。

  _grokLine(line) {
    if (!line.startsWith('{')) return;   // grok_session_id= 标记行等，跳过
    let d;
    try { d = JSON.parse(line); } catch { return; }
    switch (d.type) {
      case 'thought': this._grokToken('thought', d.data || ''); break;
      case 'text':    this._grokToken('text',    d.data || ''); break;
      case 'end':     this._grokEnd(d);                         break;
      // 其他未知 type：静默忽略
    }
  }

  // 核心：处理单个 token，自动检测类型切换并开新段落
  _grokToken(type, token) {
    // ── 类型切换：刷新上一段，重置状态 ──
    if (this._grokCurrentType !== type) {
      this._grokFlushCurrent();
      this._grokCurrentType    = type;
      this._grokCurrentBuf     = '';
      this._grokCurrentEl      = null;
      this._grokCurrentTextRef = null;
      this._grokSegCount++;
    }

    this._grokCurrentBuf += token;

    // ── 本段第一个 token：创建对应气泡元素 ──
    if (!this._grokCurrentEl) {
      const segId = `gk${this.idPrefix}${this._grokSegCount}`;
      if (type === 'thought') {
        const el = document.createElement('div');
        el.className = 'chat-bubble-wrap thought-reply timeline-node is-muted';
        el.innerHTML = `
  <div class="bubble-body">
    <div class="bubble-label think-label">思考过程</div>
    <div class="bubble think-bubble" id="${segId}"></div>
  </div>`;
        this._appendNode(el);
        this._grokCurrentEl = el.querySelector(`#${segId}`);
      } else {
        // text：需要复制按钮持有当前段文本的引用
        const textRef = { value: '' };
        this._grokCurrentTextRef = textRef;
        const el = document.createElement('div');
        el.className = 'chat-bubble-wrap ai-reply timeline-node';
        el.innerHTML = `
  <div class="bubble-body">
    <div class="bubble">
      <div class="bubble-label-row">
        <div class="bubble-label">Grok</div>
        <button class="bubble-copy-btn" title="复制">${copyBtnSVG()}</button>
      </div>
      <div class="bubble-content" id="${segId}"></div>
    </div>
  </div>`;
        el.querySelector('.bubble-copy-btn').addEventListener('click', () => {
          copyTextToClipboard(textRef.value, el.querySelector('.bubble-copy-btn'));
        });
        // 把上一条文本气泡移入折叠区（如有）
        if (this.lastBubbleEl) {
          if (this.foldProcess) {
            this._appendToProcess(this.lastBubbleEl);
          } else {
            this.inner.appendChild(this.lastBubbleEl);
          }
        }
        this.inner.appendChild(el);
        this.lastBubbleEl = el;
        this._grokCurrentEl = el.querySelector(`#${segId}`);
      }
    }

    // 同步更新复制按钮的文本引用
    if (this._grokCurrentTextRef) {
      this._grokCurrentTextRef.value = this._grokCurrentBuf;
    }

    this._grokScheduleRender();
  }

  // RAF 节流：每帧最多调用一次 renderMd，实时 markdown 渲染不卡顿
  _grokScheduleRender() {
    if (this._grokRafPending) return;
    this._grokRafPending = true;
    requestAnimationFrame(() => {
      this._grokRafPending = false;
      if (this._grokCurrentEl && this._grokCurrentBuf) {
        this._grokCurrentEl.innerHTML = renderMd(this._grokCurrentBuf);
      }
    });
  }

  // 同步刷新当前段（类型切换 / end 事件时调用）
  _grokFlushCurrent() {
    if (this._grokCurrentEl && this._grokCurrentBuf) {
      this._grokCurrentEl.innerHTML = renderMd(this._grokCurrentBuf);
      if (this._grokCurrentTextRef) {
        this._grokCurrentTextRef.value = this._grokCurrentBuf;
      }
    }
  }

  _grokEnd(d) {
    // 清掉待处理的 RAF，同步完成最后一段渲染
    this._grokRafPending = false;
    this._grokFlushCurrent();

    // 用量 / 会话信息行
    const items = [];
    if (d.stopReason) items.push(`停止原因 <span>${esc(d.stopReason)}</span>`);
    const sessionId = d.sessionId || d.session_id || d.sessionID;
    if (sessionId) {
      const sid = String(sessionId);
      items.push(`Session <span title="${esc(sid)}">#${esc(sid.slice(0, 8))}</span>`);
    }
    if (items.length) {
      const el = document.createElement('div');
      el.className = 'chat-usage timeline-node is-muted';
      el.innerHTML = items.map(i => `<div class="usage-item">${i}</div>`).join('');
      this._appendNode(el);
    }

    if (!this._footerRendered) this._renderFooter('done');
  }

  // ── Hermes 纯文本解析器 ───────────────────────────────────
  // line 到达此处时已被 trimEnd() + trim()，空行已被调用方过滤
  _hermesLine(line) {
    // 判断是否为纯分隔线（仅含 ─ U+2500）
    const isBorder = line.length > 4 && /^─+$/.test(line);

    if (this._hermesState === 'in_response') {
      if (isBorder) {
        // 遇到关闭边框，输出 AI 气泡
        const response = this._hermesResponseBuf.join('\n').trim();
        if (response) this._bubble(response, 'Hermes');
        this._hermesResponseBuf = [];
        this._hermesState = 'init';
      } else if (line.includes('⚕ Hermes')) {
        // 连续第二条回复头（多轮）：先刷出当前
        const prev = this._hermesResponseBuf.join('\n').trim();
        if (prev) this._bubble(prev, 'Hermes');
        this._hermesResponseBuf = [];
      } else {
        this._hermesResponseBuf.push(line);
      }
      return;
    }

    // init 状态
    if (isBorder) return;
    if (line.startsWith('Query:')) return;          // header 里已有 prompt 字段，不重复
    if (line === 'Initializing agent...') {
      this._pill('Hermes', '初始化中');
      return;
    }
    if (line.includes('⚕ Hermes')) {
      this._hermesState = 'in_response';
      this._hermesResponseBuf = [];
      return;
    }
    if (line.startsWith('Resume this session with:')) return;
    if (line.startsWith('hermes --resume')) return;
    if (line.startsWith('Session:')) {
      this._hermesSessionId = line.replace(/^Session:\s*/, '');
      return;
    }
    if (line.startsWith('Duration:')) {
      this._hermesDuration = line.replace(/^Duration:\s*/, '');
      return;
    }
    if (line.startsWith('Messages:')) {
      this._hermesMessages = line.replace(/^Messages:\s*/, '');
      this._hermesFlushUsage();
      return;
    }
    // 其余未知行静默忽略（避免红色 raw 样式污染）
  }

  _hermesFlushUsage() {
    const items = [];
    if (this._hermesDuration) items.push(`耗时 <span>${esc(this._hermesDuration)}</span>`);
    if (this._hermesMessages) items.push(`消息 <span>${esc(this._hermesMessages)}</span>`);
    if (this._hermesSessionId) {
      const sid = this._hermesSessionId;
      items.push(`Session <span title="${esc(sid)}">${esc(sid.slice(0, 20))}</span>`);
    }
    if (!items.length) return;
    const el = document.createElement('div');
    el.className = 'chat-usage timeline-node is-muted';
    el.innerHTML = items.map(i => `<div class="usage-item">${i}</div>`).join('');
    this._appendNode(el);
    // 重置，避免多轮重复输出
    this._hermesDuration = '';
    this._hermesMessages = '';
    this._hermesSessionId = '';
  }

  // ── 原始/错误行 ───────────────────────────────────────────
  _rawLine(line) {
    if (!line.trim()) return;
    const el = document.createElement('div');
    el.className = 'chat-raw-line';
    el.classList.add('timeline-node', 'is-error');
    el.textContent = line;
    this._appendNode(el);
  }
}
