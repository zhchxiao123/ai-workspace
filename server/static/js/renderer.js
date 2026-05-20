// ══════════════════════════════════════════════════════════════
//  ChatLogRenderer — 把 JSONL 日志渲染成对话
// ══════════════════════════════════════════════════════════════
class ChatLogRenderer {
  constructor(container, isRunning = false, foldProcess = false) {
    this.container = container;   // #log-content div
    this.inner = null;        // .chat-log div
    this.toolMap = {};          // tool_use_id → { headerEl, badgeEl, outputEl, exitEl, toolName, input, wrap }
    this._buf = '';          // SSE 行缓冲
    this._footerRendered = false;
    this.isRunning = isRunning;   // 是否正在运行
    this.foldProcess = foldProcess; // 是否折叠最新步骤

    // 折叠辅助字段
    this.processWrapper = null;
    this.processBody = null;
    this.toolCount = 0;
    this.idPrefix = Math.random().toString(36).slice(2, 8);
    this.lastBubbleEl = null;   // 保存最后一条 AI 回复节点的引用
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

  // ── 全量渲染 ─────────────────────────────────────────────
  render(text) {
    this.container.innerHTML = '<div class="chat-log timeline"></div>';
    this.inner = this.container.querySelector('.chat-log');
    this.toolMap = {};
    this._buf = '';
    this._footerRendered = false;
    this.processWrapper = null;
    this.processBody = null;
    this.toolCount = 0;
    this.lastBubbleEl = null;

    const lines = text.split('\n');
    let state = 'before';   // before | header | body | footer
    let headerLines = [];
    let footerLines = [];

    for (const raw of lines) {
      const line = raw.trimEnd();
      if (state === 'before') {
        if (line.startsWith('=== AICM Task Log ===')) { state = 'header'; headerLines = [line]; }
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
    // 跳过已渲染的头尾标记（初次全量加载后 SSE 会重复推）
    if (line.startsWith('=== AICM') || line.startsWith('======')) return;
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
      // 其余静默忽略
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

  // ── AI 文字气泡 ───────────────────────────────────────────
  _bubble(text, model) {
    const label = model
      ? model.replace(/^claude-/, '').split('-').slice(0, 2).join('-')
      : 'AI';
    const el = document.createElement('div');
    el.className = 'chat-bubble-wrap';
    el.classList.add('timeline-node');
    el.innerHTML = `
  <div class="chat-avatar ai" aria-hidden="true">AI</div>
  <div class="bubble-body">
    <div class="bubble-label">${esc(label)}</div>
    <div class="bubble">${renderMd(text)}</div>
  </div>`;

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
