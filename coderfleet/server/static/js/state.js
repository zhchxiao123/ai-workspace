
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
let chatConversationsList = [];
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
let currentChatProjectName = '';
let pendingImages = [];
let chatUploadingImages = 0;
let chatUserScrolledUp = false;
let chatModelByConversation = {};   // 会话 id -> 本次会话选中的 --model 值（仅当前会话期内记忆）
// 注：气泡翻译相关状态（systemLlmConfigured / bubbleTranslations）声明在 utils.js，
// 因为移动端只加载 utils.js + renderer.js，不加载本文件。
const CHAT_PROJECT_VISIBLE_LIMIT = 5;
let chatExpandedProjectNames = new Set();
let chatCollapsedProjectNames = (function() {
  try {
    const saved = JSON.parse(localStorage.getItem('coderfleet.chatCollapsedProjects') || '[]');
    return new Set(Array.isArray(saved) ? saved : []);
  } catch { return new Set(); }
})();
let chatPinnedProjectNames = (function() {
  try {
    const saved = JSON.parse(localStorage.getItem('coderfleet.chatPinnedProjects') || '[]');
    return new Set(Array.isArray(saved) ? saved : []);
  } catch { return new Set(); }
})();
let chatProjectSortOrder = localStorage.getItem('coderfleet.chatProjectSort') || 'default';
let chatSearchQuery = '';
let chatSearchTimer = null;
let _isRenamingConversation = false;
let _isEditingQueueItem = false;
let chatSearchResults = [];
let chatSearchLoading = false;
let chatSearchDeep = false;
let chatMentionOpen = false;
let chatMentionQuery = '';
let chatMentionStart = -1;
let chatMentionResults = [];
let chatMentionActiveIndex = 0;
let chatMentionTimer = null;
let chatMentionRequestSeq = 0;
let chatSlashOpen = false;
let chatSlashStart = -1;
let chatSlashResults = [];
let chatSlashActiveIndex = 0;
let chatSlashRequestSeq = 0;
let chatSlashSkillsCache = { account: null, skills: [] };
let tasksCache = [];
const _finishedLogCache = new Map(); // task.id -> 已完成任务的日志文本，内容不会再变，避免整体重渲染时重新拉取
// 用户手动展开过的折叠历史任务 id 集合；renderChatWorkspace 每次重渲染（如心跳触发）都会
// 从头重建 DOM，若不记录这个集合，展开过的历史任务会在下一次重渲染时又变回折叠占位。
const chatExpandedTaskIds = new Set();
let submitContext = { surface: 'task', projectName: '', boardCardId: '' };
let taskRowsCache = [];
let taskPage = 1;
const TASK_PAGE_SIZE = 12;
let globalAccountsCache = [];
let accountTypesCache = [];   // 账号类型注册表缓存，由 loadAccountTypes() 填充
let boardsCache = [];
let boardCardsCache = [];
let activeBoardId = localStorage.getItem('coderfleet.activeBoardId') || '';
let editingBoardCardId = '';

// ── 工作流 ────────────────────────────────────────────────
let activePipelineId = null;
let workflowSelectedTaskId = null;
let pipelinesCache = [];
let workflowTasksCache = [];
let wfActiveTab = 'templates';
let wfRunsFilter = 'all';   // 'all' | 'running' | 'failed' | 'done'
let dagZoom = 1.0;

// ── 工作流模板 ────────────────────────────────────────────
let activeTemplateId = null;
let templatesCache = [];
let templateDirty = false;
let _nodeCounter = 0;

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
  const label = document.getElementById('sidebar-toggle-label');
  layout.classList.toggle('sidebar-collapsed', collapsed);
  btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  btn.setAttribute('aria-label', collapsed ? '展开侧边栏' : '折叠侧边栏');
  btn.setAttribute('title', collapsed ? '展开侧边栏' : '折叠侧边栏');
  if (label) label.textContent = collapsed ? '展开侧边栏' : '折叠侧边栏';
}

function initSidebarState() {
  applySidebarCollapsed(localStorage.getItem('coderfleet.sidebarCollapsed') === 'true');
}

function toggleSidebar() {
  const layout = document.querySelector('.layout');
  const collapsed = !(layout?.classList.contains('sidebar-collapsed'));
  localStorage.setItem('coderfleet.sidebarCollapsed', collapsed ? 'true' : 'false');
  applySidebarCollapsed(collapsed);
}

function applyChatSidebarCollapsed(collapsed) {
  const layout = document.querySelector('#page-chat .chat-layout');
  const btn = document.getElementById('chat-sidebar-toggle');
  const icon = document.getElementById('chat-sidebar-toggle-icon');
  if (!layout || !btn || !icon) return;

  layout.classList.toggle('chat-sidebar-collapsed', collapsed);
  btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  btn.setAttribute('aria-label', collapsed ? '显示对话侧边栏' : '隐藏对话侧边栏');
  btn.setAttribute('title', collapsed ? '显示对话侧边栏' : '隐藏对话侧边栏');
  icon.textContent = collapsed ? '>' : '<';
}

function initChatSidebarState() {
  applyChatSidebarCollapsed(localStorage.getItem('coderfleet.chatSidebarCollapsed') === 'true');
}

function toggleChatSidebar() {
  const layout = document.querySelector('#page-chat .chat-layout');
  const collapsed = !(layout?.classList.contains('chat-sidebar-collapsed'));
  localStorage.setItem('coderfleet.chatSidebarCollapsed', collapsed ? 'true' : 'false');
  applyChatSidebarCollapsed(collapsed);
}
