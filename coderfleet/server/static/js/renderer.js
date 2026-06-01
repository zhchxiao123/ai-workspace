// ══════════════════════════════════════════════════════════════
//  ChatLogRenderer — 把 JSONL 日志渲染成对话
// ══════════════════════════════════════════════════════════════
class ChatLogRenderer {
  constructor(container, isRunning = false, foldProcess = false, projectName = null) {
    this.container = container;   // #log-content div
    this.inner = null;        // .chat-log div
    this.toolMap = {};          // tool_use_id → { headerEl, badgeEl, outputEl, exitEl, toolName, input, wrap }
    this._buf = '';          // SSE 行缓冲
    this._footerRendered = false;
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
    this._buf = '';
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
    let footerLines = [];

    for (const raw of lines) {
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
        if (line.startsWith('======')) { state = 'footer'; continue; }
        if (line.trim()) this._processLine(line.trim());
        continue;
      }
      if (state === 'footer') {
        if (line.trim()) footerLines.push(line);
      }
    }

    if (footerLines.length) this._renderFooter(footerLines.join(' '));
  }

  // ── 增量推送（SSE 每行调用一次）─────────────────────────
  push(line) {
    if (!line.trim()) return;

    if (line.startsWith('=== CoderFleet Task Log ===') || line.startsWith('=== AICM Task Log ===')) {
      if (this.inner && this.inner.querySelector('.chat-meta-block')) {
        return;
      }
      if (this.inner) {
        this.inner.innerHTML = '';
      }
      this.toolMap = {};
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
      if (!this._footerRendered) this._renderFooter(line);
      return;
    }
    this._processLine(line.trim());
  }

  // ── 私有：单行处理 ────────────────────────────────────────
  _processLine(line) {
    if (this.accountType === 'hermes') { this._hermesLine(line); return; }
    if (this.accountType === 'grok')   { this._grokLine(line);   return; }
    if (!line.startsWith('{')) { this._rawLine(line); return; }
    let d;
    try { d = JSON.parse(line); } catch { this._rawLine(line); return; }
    this._event(d);
  }

  // ── 私有：事件分发 ────────────────────────────────────────
  _event(d) {
    switch (d.type) {
      // Claude
      case 'system': return this._claudeSys(d);
      case 'assistant': return this._claudeAssistant(d.message);
      case 'user': return this._claudeUser(d.message);
      case 'result': return this._claudeResult(d);
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
    }
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
        if (typeof b.content === 'string') text = b.content;
        else if (Array.isArray(b.content)) text = b.content.map(c => c.text || '').join('\n');
        this._fillTool(b.tool_use_id, text, b.is_error);
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
    const el = document.createElement('div');
    el.className = 'chat-bubble-wrap';
    el.classList.add('timeline-node');
    el.innerHTML = `
  <div class="chat-avatar ai" aria-hidden="true">AI</div>
  <div class="bubble-body">
    <div class="bubble">
      <div class="bubble-label-row">
        <div class="bubble-label">${esc(label)}</div>
        <button class="bubble-copy-btn" title="复制">${copyBtnSVG()}</button>
      </div>
      <div class="bubble-content">${renderMd(displayText)}</div>
    </div>
  </div>`;

    el.querySelector('.bubble-copy-btn').addEventListener('click', () => {
      copyTextToClipboard(text, el.querySelector('.bubble-copy-btn'));
    });

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
    el.className = 'chat-bubble-wrap';
    el.classList.add('timeline-node', 'is-muted');
    el.innerHTML = `
  <div class="chat-avatar think" aria-hidden="true">TH</div>
  <div class="bubble-body">
    <div class="bubble-label think-label">思考过程</div>
    <div class="bubble think-bubble">${renderMd(text)}</div>
  </div>`;
    this._appendNode(el);
  }

  // ── 工具调用卡片 ──────────────────────────────────────────
  _toolUse(block) {
    const { id, name, input } = block;
    const summary = formatToolSummary(name, input);
    const detail = formatToolInput(name, input);
    const icon = toolIcon(name);
    const hasInput = detail && detail !== summary;

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
    <button class="tool-toggle" id="tt-${id}">展开</button>
  </div>
  <div class="tool-body collapsed" id="tbody-${id}">
    ${hasInput ? `<div class="tool-input">${esc(detail)}</div>` : ''}
    <div class="tool-output" id="to-${id}" style="display:none"></div>
    <div class="tool-exit"   id="te-${id}" style="display:none"></div>
  </div>`;

    wrap.appendChild(card);
    this._appendNode(wrap);

    // Toggle 逻辑
    const header = card.querySelector('.chat-tool-header');
    const body = card.querySelector(`#tbody-${id}`);
    const btn = card.querySelector(`#tt-${id}`);
    header.addEventListener('click', () => {
      const collapsed = body.classList.toggle('collapsed');
      btn.textContent = collapsed ? '展开' : '收起';
    });
    btn.addEventListener('click', e => { e.stopPropagation(); header.click(); });

    this.toolMap[id] = {
      statusEl: card.querySelector(`#ts-${id}`),
      badgeEl: card.querySelector(`#tb-${id}`),
      outputEl: card.querySelector(`#to-${id}`),
      exitEl: card.querySelector(`#te-${id}`),
      bodyEl: body,
      btnEl: btn,
      wrap, name, input,
    };
  }

  // ── 填充工具结果 ──────────────────────────────────────────
  _fillTool(id, text, isError) {
    const e = this.toolMap[id];
    if (!e) return;

    const ok = !isError;
    e.statusEl.textContent = ok ? '✓' : '✗';
    e.badgeEl.className = `tool-badge ${ok ? 'ok' : 'fail'}`;
    e.badgeEl.textContent = `${toolIcon(e.name)} ${e.name}`;

    const out = (text || '').trim();
    e.outputEl.style.display = '';
    e.outputEl.className = `tool-output${ok ? '' : ' is-error'}`;
    e.outputEl.textContent = out || (ok ? '(无输出)' : '(执行失败)');

    e.exitEl.style.display = '';
    e.exitEl.className = `tool-exit ${ok ? 'ok-exit' : 'fail-exit'}`;
    e.exitEl.textContent = ok ? '✓  exit 0' : '✗  error';

    // 短输出自动展开
    const lineCount = out.split('\n').length;
    if (lineCount <= 6 && out.length <= 400) {
      e.bodyEl.classList.remove('collapsed');
      e.btnEl.textContent = '收起';
    }

    // 文件操作：追加变更徽章
    if (['Write', 'Edit', 'NotebookEdit'].includes(e.name) && ok) {
      const fp = e.input?.file_path || e.input?.path || '';
      if (fp) {
        const kind = e.name === 'Write' ? 'create' : 'edit';
        const label = e.name === 'Write' ? 'CREATE' : 'EDIT';
        const fc = document.createElement('div');
        fc.className = 'chat-file-card timeline-node';
        fc.innerHTML = `<span class="file-op-badge ${kind}">${label}</span><span class="file-path">${esc(fp)}</span>`;
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
        el.className = 'chat-bubble-wrap timeline-node is-muted';
        el.innerHTML = `
  <div class="chat-avatar think" aria-hidden="true">TH</div>
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
        el.className = 'chat-bubble-wrap timeline-node';
        el.innerHTML = `
  <div class="chat-avatar ai" aria-hidden="true">AI</div>
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
    if (d.sessionId) {
      const sid = String(d.sessionId);
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
