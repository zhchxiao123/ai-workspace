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
};

function toolIcon(name) { return TOOL_ICONS[name] || 'TL'; }

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
  try { return JSON.stringify(input, null, 2); } catch { return String(input); }
}

// ── 安全 HTML 转义 ─────────────────────────────────────────
function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
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
  const d = new Date(iso), now = new Date(), diff = (now - d) / 1000;
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
  return { running: '运行中', done: '完成', failed: '失败', killed: '已终止' }[s] || s;
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

// 用于用户气泡（onclick 内联调用）
function copyUserBubble(btn) {
  const content = btn.closest('.user-bubble').querySelector('.user-bubble-content');
  copyTextToClipboard(content.textContent, btn);
}

