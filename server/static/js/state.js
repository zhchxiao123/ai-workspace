
// ══════════════════════════════════════════════════════════════
//  页面状态
// ══════════════════════════════════════════════════════════════
let currentPage = 'chat';
let currentTaskId = null;
let currentTaskData = null;
let sseSource = null;
let followMode = false;
let renderer = null;
let conversationsCache = {};
let projectsCache = [];
let projectContext = null;
let projectDashboardData = { tasks: [], accounts: [] };

// ── AI 对话（聊天室）逻辑 ───────────────────────────────────
let activeConversationId = null;
let sseChatSource = null;
let chatFollowMode = false;
let currentChatTaskId = null;
let chatRenderer = null;
let chatNewSessionProject = '';
let tasksCache = [];
let terminalContext = {
  projectName: '',
  socket: null,
  terminal: null,
  fitAddon: null,
  connected: false,
  resizeTimer: null,
  lastState: 'closed',
};
let submitContext = { surface: 'task', projectName: '' };
let taskRowsCache = [];
let taskPage = 1;
const TASK_PAGE_SIZE = 12;
let globalAccountsCache = [];

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function updateTaskMetrics(tasks = [], accounts = []) {
  const count = status => tasks.filter(t => t.status === status).length;
  setText('metric-running', count('running'));
  setText('metric-done', count('done'));
  setText('metric-failed', count('failed'));
  setText('metric-accounts', accounts.length || '-');
  const online = accounts.filter(a => a.running).length;
  setText('metric-account-note', accounts.length ? `${online}/${accounts.length} 在线` : '暂无账号数据');
}

function applySidebarCollapsed(collapsed) {
  const layout = document.querySelector('.layout');
  const btn = document.getElementById('sidebar-toggle');
  if (!layout || !btn) return;
  layout.classList.toggle('sidebar-collapsed', collapsed);
  btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  btn.setAttribute('aria-label', collapsed ? '展开侧边栏' : '折叠侧边栏');
  btn.setAttribute('title', collapsed ? '展开侧边栏' : '折叠侧边栏');
}

function initSidebarState() {
  applySidebarCollapsed(localStorage.getItem('aicm.sidebarCollapsed') === 'true');
}

function toggleSidebar() {
  const layout = document.querySelector('.layout');
  const collapsed = !(layout?.classList.contains('sidebar-collapsed'));
  localStorage.setItem('aicm.sidebarCollapsed', collapsed ? 'true' : 'false');
  applySidebarCollapsed(collapsed);
}

