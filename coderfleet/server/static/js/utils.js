'use strict';
const API = '';
const SIDEBAR_COLLAPSED_KEY = 'coderfleet.sidebarCollapsed';

// ══════════════════════════════════════════════════════════════
//  API Key 认证
// ══════════════════════════════════════════════════════════════
const _AUTH_KEY_STORAGE = 'coderfleet.apiKey';

function getApiKey() { return localStorage.getItem(_AUTH_KEY_STORAGE) || ''; }
function setApiKey(k) { localStorage.setItem(_AUTH_KEY_STORAGE, k); }
function clearApiKey() { localStorage.removeItem(_AUTH_KEY_STORAGE); }

// 覆盖全局 fetch，自动注入 Authorization 头
const _origFetch = window.fetch.bind(window);
window.fetch = async function (input, init) {
  init = init || {};
  const key = getApiKey();
  if (key) {
    const headers = new Headers(init.headers || {});
    if (!headers.has('Authorization')) headers.set('Authorization', `Bearer ${key}`);
    init = { ...init, headers };
  }
  const resp = await _origFetch(input, init);
  if (resp.status === 401) showLoginOverlay();
  return resp;
};

// WebSocket URL：自动附加 ?token=<key>
function wsUrl(path) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const key = getApiKey();
  const q = key ? `?token=${encodeURIComponent(key)}` : '';
  return `${proto}//${location.host}${path}${q}`;
}

// SSE / EventSource URL：追加 &token=<key>
function sseUrl(url) {
  const key = getApiKey();
  if (!key) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}token=${encodeURIComponent(key)}`;
}

// 普通链接 / 图片 / 下载不会经过 fetch 包装，需用查询参数携带 token。
function authedUrl(url) {
  const key = getApiKey();
  if (!key) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}token=${encodeURIComponent(key)}`;
}

function showLoginOverlay() {
  const el = document.getElementById('auth-overlay');
  if (el) el.style.display = 'flex';
  // 自动聚焦输入框
  setTimeout(() => document.getElementById('auth-key-input')?.focus(), 80);
}

function hideLoginOverlay() {
  const el = document.getElementById('auth-overlay');
  if (el) el.style.display = 'none';
}

function toggleAuthKeyVisibility() {
  const input = document.getElementById('auth-key-input');
  const icon  = document.getElementById('auth-eye-icon');
  if (!input) return;
  const isText = input.type === 'text';
  input.type = isText ? 'password' : 'text';
  // 切换图标：眼睛 ↔ 划线眼睛
  if (icon) {
    icon.innerHTML = isText
      ? '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>'
      : '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>'
        + '<path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>'
        + '<line x1="1" y1="1" x2="23" y2="23"/>';
  }
}

async function submitApiKey() {
  const input  = document.getElementById('auth-key-input');
  const errEl  = document.getElementById('auth-error');
  const btnEl  = document.getElementById('auth-submit-btn');
  const key    = (input?.value || '').trim();
  if (!key) {
    if (errEl) { errEl.textContent = '请输入 API Key。'; errEl.style.display = 'block'; }
    input?.focus();
    return;
  }

  if (errEl) { errEl.style.display = 'none'; errEl.textContent = ''; }
  if (btnEl) { btnEl.textContent = '验证中…'; btnEl.classList.add('loading'); btnEl.disabled = true; }

  try {
    const resp = await _origFetch(`${API}/api/accounts`, {
      headers: { 'Authorization': `Bearer ${key}` },
    });

    if (resp.ok) {
      setApiKey(key);
      location.reload();
    } else {
      if (errEl) { errEl.textContent = 'API Key 无效，请检查后重试。'; errEl.style.display = 'block'; }
      input?.select();
    }
  } catch {
    if (errEl) { errEl.textContent = '网络错误，无法连接服务器。'; errEl.style.display = 'block'; }
  } finally {
    if (btnEl) { btnEl.textContent = '确认登录'; btnEl.classList.remove('loading'); btnEl.disabled = false; }
  }
}

function logoutApiKey() {
  clearApiKey();
  location.reload();
}

// ══════════════════════════════════════════════════════════════
//  工具图标 & 格式化
// ══════════════════════════════════════════════════════════════
const TOOL_ICONS = {
  Bash: 'SH', Read: 'RD', Write: 'WR', Edit: 'ED', Glob: 'GB', Grep: 'GR',
  WebFetch: 'WF', WebSearch: 'WS', Agent: 'AG', Task: 'TK',
  NotebookEdit: 'NB', ExitPlanMode: 'OK', EnterPlanMode: 'PL',
  TodoWrite: 'TD', AskUserQuestion: 'Q', Monitor: 'MN', Skill: 'SK',
  EnterWorktree: 'WT', ExitWorktree: 'WT',
  // CC 较新版本内置的 agent/编排类工具（子agent任务队列、定时唤醒、
  // 报告发现、跨会话消息等）——图标先补上，摘要格式化只对 schema
  // 确定的几个做（见 formatToolSummary），其余沿用默认 JSON 预览，
  // 不猜字段名导致展示空白。
  TaskCreate: 'TC', TaskUpdate: 'TU', TaskGet: 'TG', TaskList: 'TQ',
  TaskOutput: 'TP', TaskStop: 'TX',
  CronCreate: 'CC', CronDelete: 'CD', CronList: 'CN',
  DesignSync: 'DS', PushNotification: 'PN', RemoteTrigger: 'RT',
  ReportFindings: 'RF', ScheduleWakeup: 'SW', SendMessage: 'SM',
  ToolSearch: 'TS', Workflow: 'WK',
};

function toolIcon(name) { return TOOL_ICONS[name] || 'TL'; }

// CC's Monitor 工具用毫秒（timeout_ms），格式化成人可读的 "2m 30s" 风格。
function formatTimeoutMs(ms) {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms <= 0) return '';
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remSeconds = seconds % 60;
  if (minutes < 60) return remSeconds > 0 ? `${minutes}m ${remSeconds}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remMinutes = minutes % 60;
  return remMinutes > 0 ? `${hours}h ${remMinutes}m` : `${hours}h`;
}

function formatToolSummary(name, input) {
  if (!input) return '';
  switch (name) {
    case 'Bash': return input.command || '';
    case 'Read': return input.file_path || input.path || '';
    case 'Write': return input.file_path || input.path || '';
    case 'Edit': return input.file_path || input.path || '';
    case 'Glob': return (input.pattern || '') + (input.path ? ' in ' + input.path : '');
    case 'Grep': return '"' + (input.pattern || '') + '"' + (input.path ? ' in ' + input.path : '');
    case 'WebFetch': return input.url || '';
    case 'WebSearch': return input.query || '';
    case 'Monitor': {
      const chip = (input.description || input.command || '').trim();
      const timeout = formatTimeoutMs(input.timeout_ms);
      return chip + (timeout ? ` · ${timeout}` : '');
    }
    case 'Skill': return input.skill || input.name || input.command || '';
    case 'EnterWorktree': return input.name || input.path || '';
    case 'ExitWorktree': {
      const verb = input.action === 'remove' ? '移除 worktree' : '离开 worktree';
      return verb + (input.action === 'remove' && input.discard_changes ? ' · 丢弃改动' : '');
    }
    case 'TaskCreate': return input.subject || '';
    case 'TaskUpdate': return `#${input.taskId ?? ''}` + (input.status ? ` → ${input.status}` : '');
    case 'ReportFindings': {
      const n = Array.isArray(input.findings) ? input.findings.length : 0;
      return n ? `${n} 项发现` : '(无发现)';
    }
    case 'ScheduleWakeup': {
      if (input.stop) return '停止循环';
      const secs = input.delaySeconds;
      const when = secs != null ? `${secs}s 后` : '';
      return [when, input.reason || ''].filter(Boolean).join(' · ');
    }
    default: {
      const s = JSON.stringify(input);
      return s.length > 100 ? s.slice(0, 100) + '…' : s;
    }
  }
}

function formatToolInput(name, input) {
  if (!input) return '';
  if (name === 'Bash') return input.command || '';
  if (name === 'Write') {
    const preview = (input.content || '').slice(0, 300);
    return `file: ${input.file_path || ''}\n${preview}${(input.content || '').length > 300 ? '\n…' : ''}`;
  }
  if (name === 'Edit') {
    return `file: ${input.file_path || ''}\n- old: ${(input.old_string || '').slice(0, 120)}\n+ new: ${(input.new_string || '').slice(0, 120)}`;
  }
  if (name === 'Monitor') {
    const parts = [];
    if (input.command) parts.push(`command: ${input.command}`);
    if (input.description) parts.push(`description: ${input.description}`);
    const timeout = formatTimeoutMs(input.timeout_ms);
    if (timeout) parts.push(`timeout: ${timeout}`);
    if (input.run_in_background != null) parts.push(`run_in_background: ${input.run_in_background}`);
    return parts.join('\n');
  }
  try {
    const s = JSON.stringify(input, null, 2);
    return s.length > 2000 ? s.slice(0, 2000) + '\n…' : s;
  } catch { return String(input); }
}

// ── Edit 工具的行级 diff 渲染 ───────────────────────────────
// old_string/new_string 通常只是文件里的一小段上下文，用标准 LCS 动态规划
// 逐行对齐即可；只有当两侧行数乘积过大（罕见的超大段 Edit）时才退化为
// 整体删除+整体新增，避免 O(n*m) 的表格在这种输入上过度分配内存。
function _lcsLineDiff(aLines, bLines) {
  const n = aLines.length, m = bLines.length;
  const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = aLines[i] === bLines[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (aLines[i] === bLines[j]) { ops.push({ type: 'ctx', text: aLines[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push({ type: 'del', text: aLines[i] }); i++; }
    else { ops.push({ type: 'add', text: bLines[j] }); j++; }
  }
  while (i < n) { ops.push({ type: 'del', text: aLines[i] }); i++; }
  while (j < m) { ops.push({ type: 'add', text: bLines[j] }); j++; }
  return ops;
}

function renderInlineDiff(oldStr, newStr) {
  const aLines = String(oldStr ?? '').split('\n');
  const bLines = String(newStr ?? '').split('\n');
  const ops = (aLines.length * bLines.length > 200000)
    ? [...aLines.map(t => ({ type: 'del', text: t })), ...bLines.map(t => ({ type: 'add', text: t }))]
    : _lcsLineDiff(aLines, bLines);

  return ops.map(op => {
    const cls = op.type === 'add' ? 'diff-add' : op.type === 'del' ? 'diff-del' : 'diff-ctx';
    const sign = op.type === 'add' ? '+' : op.type === 'del' ? '-' : ' ';
    return `<div class="diff-line ${cls}"><span class="diff-sign">${sign}</span><span class="diff-text">${esc(op.text)}</span></div>`;
  }).join('');
}

// ── AskUserQuestion 工具参数归一化 ──────────────────────────
// tool_use.input 理论上是 { questions: [{question, header, options, multiSelect}] }，
// 但模型/CLI 版本偶尔会给出单个 question 对象而非数组，或整段被序列化成字符串，
// 所以按字段逐一容错，拿不到 question 文本的条目直接丢弃，不让一张坏卡片炸掉整个对话。
function normalizeAskUserQuestions(args) {
  let a = args;
  if (typeof a === 'string') {
    try { a = JSON.parse(a); } catch { return []; }
  }
  if (!a || typeof a !== 'object') return [];

  let rawQuestions = a.questions;
  if (typeof rawQuestions === 'string') {
    try { rawQuestions = JSON.parse(rawQuestions); } catch { rawQuestions = null; }
  }
  if (!Array.isArray(rawQuestions)) {
    rawQuestions = a.question ? [a] : [];
  }

  return rawQuestions.map(q => {
    if (!q || typeof q !== 'object' || !q.question) return null;
    const options = Array.isArray(q.options)
      ? q.options.map(o => {
        if (!o || typeof o !== 'object' || !o.label) return null;
        return o.description
          ? { label: String(o.label), description: String(o.description) }
          : { label: String(o.label) };
      }).filter(Boolean)
      : [];
    return {
      question: String(q.question),
      header: q.header ? String(q.header) : '',
      multiSelect: !!q.multiSelect,
      options,
    };
  }).filter(Boolean);
}

// ── Read 工具行号剥离 ───────────────────────────────────────
// Claude Code 的 Read 结果固定用 `cat -n` 式行号前缀（"␣␣␣1\t内容"），
// 对着行号看代码没有意义，展示前按行剥掉。
function stripReadLineNumbers(text) {
  if (!text) return text || '';
  return text.split('\n').map(line => line.replace(/^\s*\d+\t/, '')).join('\n');
}

// ── 安全 HTML 转义 ─────────────────────────────────────────
function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── ANSI 转义码 → 带色 HTML ──────────────────────────────────
// Bash 类工具的输出（测试跑分、linter、`git diff --color` 等）经常带着 ANSI
// SGR 颜色码；之前这些码要么原样显示成乱码，要么被整体吞掉。这里只解析颜色/
// 样式相关的 SGR 序列（`\x1b[...m`），其余 CSI 序列（光标移动等，terminal-only，
// 没有意义直接丢弃）连同裸 \r 一起剥掉。
const ANSI_16 = [
  '#000000', '#cc0000', '#4e9a06', '#c4a000', '#3465a4', '#75507b', '#06989a', '#d3d7cf',
  '#555753', '#ef2929', '#8ae234', '#fce94f', '#729fcf', '#ad7fa8', '#34e2e2', '#eeeeec',
];

function _ansi256ToHex(n) {
  n = n | 0;
  if (n < 16) return ANSI_16[n];
  if (n <= 231) {
    const cube = [0, 95, 135, 175, 215, 255];
    const i = n - 16;
    const rgb = [cube[Math.floor(i / 36) % 6], cube[Math.floor(i / 6) % 6], cube[i % 6]];
    return '#' + rgb.map(v => v.toString(16).padStart(2, '0')).join('');
  }
  const v = Math.max(0, Math.min(255, 8 + (n - 232) * 10));
  const h = v.toString(16).padStart(2, '0');
  return '#' + h + h + h;
}

function ansiToHtml(text) {
  const raw = String(text ?? '');
  if (raw.indexOf('\x1b') === -1) return esc(raw);

  // 非 SGR 的 CSI 序列（光标移动/清行等）丢弃，SGR（以 m 结尾）留到下面解析。
  const cleaned = raw
    .replace(/\x1b\[[0-9;]*[A-Za-z]/g, seq => (seq.endsWith('m') ? seq : ''))
    .replace(/\r(?!\n)/g, '');

  let out = '';
  let openSpan = false;
  let state = { fg: null, bg: null, bold: false, dim: false, italic: false, underline: false, strike: false };

  const styleFor = s => {
    const parts = [];
    if (s.fg) parts.push(`color:${s.fg}`);
    if (s.bg) parts.push(`background:${s.bg}`);
    if (s.bold) parts.push('font-weight:700');
    if (s.dim) parts.push('opacity:.65');
    if (s.italic) parts.push('font-style:italic');
    const decos = [];
    if (s.underline) decos.push('underline');
    if (s.strike) decos.push('line-through');
    if (decos.length) parts.push(`text-decoration:${decos.join(' ')}`);
    return parts.join(';');
  };
  const closeSpan = () => { if (openSpan) { out += '</span>'; openSpan = false; } };
  const openSpanIfNeeded = () => {
    closeSpan();
    const style = styleFor(state);
    if (style) { out += `<span style="${style}">`; openSpan = true; }
  };

  const sgrRe = /\x1b\[([0-9;]*)m/g;
  let lastIndex = 0, m;
  while ((m = sgrRe.exec(cleaned))) {
    const chunk = cleaned.slice(lastIndex, m.index);
    if (chunk) out += esc(chunk);
    lastIndex = sgrRe.lastIndex;

    const codes = m[1] === '' ? [0] : m[1].split(';').map(Number);
    for (let i = 0; i < codes.length; i++) {
      const c = codes[i];
      if (c === 0) state = { fg: null, bg: null, bold: false, dim: false, italic: false, underline: false, strike: false };
      else if (c === 1) state.bold = true;
      else if (c === 2) state.dim = true;
      else if (c === 3) state.italic = true;
      else if (c === 4) state.underline = true;
      else if (c === 9) state.strike = true;
      else if (c === 22) { state.bold = false; state.dim = false; }
      else if (c === 23) state.italic = false;
      else if (c === 24) state.underline = false;
      else if (c === 29) state.strike = false;
      else if (c >= 30 && c <= 37) state.fg = ANSI_16[c - 30];
      else if (c === 38 && codes[i + 1] === 5) { state.fg = _ansi256ToHex(codes[i + 2]); i += 2; }
      else if (c === 38 && codes[i + 1] === 2) { state.fg = `rgb(${codes[i + 2]},${codes[i + 3]},${codes[i + 4]})`; i += 4; }
      else if (c === 39) state.fg = null;
      else if (c >= 40 && c <= 47) state.bg = ANSI_16[c - 40];
      else if (c === 48 && codes[i + 1] === 5) { state.bg = _ansi256ToHex(codes[i + 2]); i += 2; }
      else if (c === 48 && codes[i + 1] === 2) { state.bg = `rgb(${codes[i + 2]},${codes[i + 3]},${codes[i + 4]})`; i += 4; }
      else if (c === 49) state.bg = null;
      else if (c >= 90 && c <= 97) state.fg = ANSI_16[8 + (c - 90)];
      else if (c >= 100 && c <= 107) state.bg = ANSI_16[8 + (c - 100)];
    }
    openSpanIfNeeded();
  }
  const rest = cleaned.slice(lastIndex);
  if (rest) out += esc(rest);
  closeSpan();
  return out;
}

// ── 基础 Markdown 渲染（使用 marked.js） ──────────
function renderMd(text) {
  if (typeof marked !== 'undefined') {
    try {
      marked.setOptions({
        gfm: true,
        breaks: true,
        headerIds: false,
        mangle: false
      });
      return marked.parse(text);
    } catch (e) {
      console.error("Markdown render error: ", e);
    }
  }
  let s = esc(text);
  // 围栏代码块
  s = s.replace(/```[\w]*\n?([\s\S]*?)```/g,
    '<pre><code>$1</code></pre>');
  // 行内代码
  s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  // 粗体
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  // 斜体
  s = s.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
  // 换行
  s = s.replace(/\n/g, '<br>');
  return s;
}

// ── 时间格式化 ─────────────────────────────────────────────
function fmtTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso), now = new Date();
  let diff = (now - d) / 1000;
  // diff 明显为负说明时间戳被解析成了"未来"（客户端/服务端时钟或时区不一致）。
  // 不能再判定为"刚刚"——那会让它无论过多久都卡在"刚刚"——直接退化成绝对时间。
  if (diff < -5) return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  diff = Math.max(diff, 0);
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
  return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function fmtTimeFriendly(iso) {
  return fmtTime(iso);
}

function fmtDuration(created, finished) {
  if (!created) return '-';
  const secs = Math.round((new Date(finished || Date.now()) - new Date(created)) / 1000);
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60), s = secs % 60;
  if (secs < 3600) return s ? `${m}m${String(s).padStart(2, '0')}s` : `${m}m`;
  const h = Math.floor(secs / 3600), rm = Math.floor((secs % 3600) / 60);
  return rm ? `${h}h${String(rm).padStart(2, '0')}m` : `${h}h`;
}

function statusLabel(s) {
  return { scheduled: '等待依赖', pending: '待执行', running: '运行中', done: '完成', failed: '失败', killed: '已终止' }[s] || s;
}

function statusRank(s) {
  return ({ running: 0, failed: 1, killed: 2, done: 3 })[s] ?? 4;
}

// ── 气泡复制 ───────────────────────────────────────────────
function copyBtnSVG() {
  return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
}

function _applyBubbleCopyFeedback(btn) {
  btn.textContent = '✓';
  btn.classList.add('copied');
  setTimeout(() => { btn.innerHTML = copyBtnSVG(); btn.classList.remove('copied'); }, 1500);
}

function copyTextToClipboard(text, btn) {
  const fallback = () => {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try { document.execCommand('copy'); } catch {}
    document.body.removeChild(ta);
    _applyBubbleCopyFeedback(btn);
  };
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    navigator.clipboard.writeText(text).then(() => {
      _applyBubbleCopyFeedback(btn);
    }).catch(fallback);
  } else {
    fallback();
  }
}

// ── 气泡翻译 ───────────────────────────────────────────────
// 状态声明在此（而非 state.js）：移动端只加载 utils.js + renderer.js。
// 系统级 LLM 是否已配置，决定翻译入口是否出现
let systemLlmConfigured = false;
// 每个气泡的翻译状态缓存（键为 .bubble-content 元素，弱引用随 DOM 回收）
const bubbleTranslations = new WeakMap();

function translateBtnSVG() {
  return '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 8h9M9 4v1M12 8c0 5-3.5 8-7 9M7 8c0 4 3 6.5 6 7.5"/><path d="M14 20l3.5-8 3.5 8M15.2 17.5h4.6"/></svg>';
}

// 一段文本是否主要为非中文（据此决定要不要显示「译」按钮，纯中文气泡不打扰）
function isMostlyNonChinese(text) {
  const s = String(text || '');
  const letters = s.match(/[A-Za-z一-鿿]/g) || [];
  if (letters.length < 8) return false;               // 太短不值得
  const cjk = (s.match(/[一-鿿]/g) || []).length;
  return cjk / letters.length < 0.5;
}

// 启动时探一次系统 LLM 是否配置；未配置则所有翻译入口不出现
async function initSystemLlmStatus() {
  try {
    const r = await fetch('/api/system-llm/status');
    if (!r.ok) return;
    const d = await r.json();
    systemLlmConfigured = !!d.configured;
    document.body.classList.toggle('sysllm-ready', systemLlmConfigured);
  } catch { /* 探测失败当作未配置，静默 */ }
}
document.addEventListener('DOMContentLoaded', initSystemLlmStatus);

// 切换某个气泡的原文 / 译文。btn=触发按钮，contentEl=.bubble-content，rawText=原始 markdown
async function toggleBubbleTranslation(btn, contentEl, rawText) {
  let st = bubbleTranslations.get(contentEl);
  if (!st) { st = { showing: 'original', originalHtml: contentEl.innerHTML }; bubbleTranslations.set(contentEl, st); }

  if (st.showing === 'translated') {                  // 译文 → 原文
    contentEl.innerHTML = st.originalHtml;
    st.showing = 'original';
    btn.classList.remove('active');
    return;
  }
  if (st.translatedHtml) {                             // 命中缓存，直接切回译文
    contentEl.innerHTML = st.translatedHtml;
    st.showing = 'translated';
    btn.classList.add('active');
    return;
  }

  if (btn.classList.contains('loading')) return;      // 正在翻译，忽略重复点击
  btn.classList.add('loading');
  try {
    const r = await fetch('/api/translate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: rawText }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || `翻译失败 (${r.status})`);
    }
    const d = await r.json();
    st.originalHtml = contentEl.innerHTML;            // 以当前渲染为准
    st.translatedHtml = renderMd(d.translated || '');
    contentEl.innerHTML = st.translatedHtml;
    st.showing = 'translated';
    btn.classList.add('active');
  } catch (err) {
    btn.classList.add('failed');
    btn.title = String(err && err.message || err);
    setTimeout(() => { btn.classList.remove('failed'); btn.title = '翻译'; }, 2500);
  } finally {
    btn.classList.remove('loading');
  }
}

// 用于用户气泡（onclick 内联调用）
function copyUserBubble(btn) {
  const content = btn.closest('.user-bubble').querySelector('.user-bubble-content');
  copyTextToClipboard(content.textContent, btn);
}

// ══════════════════════════════════════════════════════════════
//  移动端共享小工具（desktop + mobile 共用）
// ══════════════════════════════════════════════════════════════

/** 简短别名（移动端历史代码常用 x()） */
function x(s) {
  return esc(s);
}

/** 去除 ANSI 转义（日志流处理） */
function stripAnsi(s) {
  return String(s ?? '').replace(/\x1b\[[0-9;]*[A-Za-z]/g, '').replace(/\r/g, '');
}

// ── 共享浮层提示（toast） ─────────────────────────────────
/**
 * 通用浮层提示。type: 'info' | 'success' | 'error' | 'warning'。
 * onClick 可选：提供时点击提示会执行该回调（例如跳转到相关记录）。
 */
function showToast(message, type = 'info', onClick) {
  let host = document.getElementById('toast-host');
  if (!host) {
    host = document.createElement('div');
    host.id = 'toast-host';
    document.body.appendChild(host);
  }
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.innerHTML = `<span>${esc(message)}</span>`;
  el.onclick = () => { if (onClick) onClick(); el.remove(); };
  host.appendChild(el);
  setTimeout(() => el.remove(), 8000);
  return el;
}

/**
 * 移动友好相对时间（与桌面 fmtTime 风格略有差异）
 */
function relTime(iso) {
  if (!iso) return '';
  const raw = Date.now() - new Date(iso);
  // 时钟偏差保护：见桌面端 fmtTime() 同名注释。
  if (raw < -5000) return new Date(iso).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  const diff = Math.max(raw, 0);
  if (diff < 60000)    return '刚刚';
  if (diff < 3600000)  return `${Math.floor(diff / 60000)} 分钟前`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`;
  return `${Math.floor(diff / 86400000)} 天前`;
}
