'use strict';
const API = '';
const SIDEBAR_COLLAPSED_KEY = 'coderfleet.sidebarCollapsed';

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
  navigator.clipboard.writeText(text).then(() => {
    _applyBubbleCopyFeedback(btn);
  }).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch {}
    document.body.removeChild(ta);
    _applyBubbleCopyFeedback(btn);
  });
}

// 用于用户气泡（onclick 内联调用）
function copyUserBubble(btn) {
  const content = btn.closest('.user-bubble').querySelector('.user-bubble-content');
  copyTextToClipboard(content.textContent, btn);
}

