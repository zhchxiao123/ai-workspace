function terminalWsUrl(projectName) {
  return wsUrl(`${API}/api/projects/${encodeURIComponent(projectName)}/terminal`);
}

function createProjectTerminalContext() {
  return {
    projectName: '',
    socket: null,
    terminal: null,
    fitAddon: null,
    connected: false,
    resizeTimer: null,
    lastState: 'closed',
  };
}

let terminalContext = createProjectTerminalContext();

function setProjectTerminalStatus(state, message) {
  terminalContext.lastState = state;
  const dot = document.getElementById('project-terminal-dot');
  const text = document.getElementById('project-terminal-status');
  if (dot) {
    dot.className = `status-dot ${state === 'connected' ? 'running' : state === 'error' ? 'failed' : 'killed'}`;
    dot.textContent = state === 'connected' ? '已连接' : state === 'error' ? '错误' : '未连接';
  }
  if (text) text.textContent = message || '';
}

function ensureProjectTerminalMounted() {
  const mount = document.getElementById('project-terminal');
  if (!mount || !window.Terminal || !window.FitAddon) return false;
  if (terminalContext.terminal) return true;
  mount.innerHTML = '';
  const { terminal, fitAddon } = createEnhancedTerminal(mount);
  terminalContext.terminal = terminal;
  terminalContext.fitAddon = fitAddon;
  terminal.onData(data => {
    const socket = terminalContext.socket;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'input', data }));
    }
  });
  resizeProjectTerminal();
  return true;
}

function openProjectTerminal(projectName = '') {
  connectProjectTerminal(projectName || projectContext?.name || '');
}

function connectProjectTerminal(projectName) {
  if (!projectName) {
    setProjectTerminalStatus('closed', '请选择项目后连接终端');
    return;
  }
  if (terminalContext.projectName === projectName && terminalContext.socket &&
      (terminalContext.socket.readyState === WebSocket.OPEN || terminalContext.socket.readyState === WebSocket.CONNECTING)) {
    resizeProjectTerminal();
    terminalContext.terminal?.focus();
    return;
  }
  disconnectProjectTerminal({ dispose: false });
  terminalContext.projectName = projectName;
  if (!ensureProjectTerminalMounted()) {
    setProjectTerminalStatus('error', '终端资源未加载，请刷新页面后重试');
    return;
  }

  setProjectTerminalStatus('closed', '正在连接项目容器...');
  terminalContext.terminal.clear();
  terminalContext.terminal.writeln(`Connecting to ${projectName}...`);

  const socket = new WebSocket(terminalWsUrl(projectName));
  terminalContext.socket = socket;
  socket.addEventListener('open', () => {
    if (terminalContext.socket !== socket) return;
    terminalContext.connected = true;
    setProjectTerminalStatus('connected', `已连接 ${projectName}`);
    resizeProjectTerminal();
    terminalContext.terminal?.focus();
  });
  socket.addEventListener('message', event => {
    if (terminalContext.socket !== socket) return;
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.type === 'output') {
      terminalContext.terminal.write(String(message.data || ''));
    } else if (message.type === 'status') {
      terminalContext.connected = message.state === 'connected';
      setProjectTerminalStatus(message.state, message.message || '');
      if (message.state === 'error') {
        terminalContext.terminal.writeln(`\r\n${message.message || '连接失败'}`);
      }
    }
  });
  socket.addEventListener('close', () => {
    if (terminalContext.socket !== socket) return;
    terminalContext.connected = false;
    if (terminalContext.lastState !== 'error') {
      setProjectTerminalStatus('closed', '终端连接已关闭');
    }
  });
  socket.addEventListener('error', () => {
    if (terminalContext.socket !== socket) return;
    terminalContext.connected = false;
    setProjectTerminalStatus('error', '终端连接失败');
  });
}

function resizeProjectTerminal() {
  if (!terminalContext.terminal || !terminalContext.fitAddon) return;
  clearTimeout(terminalContext.resizeTimer);
  terminalContext.resizeTimer = setTimeout(() => {
    try {
      terminalContext.fitAddon.fit();
      const socket = terminalContext.socket;
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({
          type: 'resize',
          cols: terminalContext.terminal.cols,
          rows: terminalContext.terminal.rows,
        }));
      }
    } catch {}
  }, 30);
}

function disconnectProjectTerminal(options = {}) {
  const { dispose = true } = options;
  const socket = terminalContext.socket;
  terminalContext.socket = null;
  terminalContext.connected = false;
  if (socket && socket.readyState !== WebSocket.CLOSED) {
    socket.close();
  }
  if (dispose && terminalContext.terminal) {
    terminalContext.terminal.dispose();
    terminalContext.terminal = null;
    terminalContext.fitAddon = null;
    const mount = document.getElementById('project-terminal');
    if (mount) mount.innerHTML = '';
  }
  clearTimeout(terminalContext.resizeTimer);
  setProjectTerminalStatus('closed', '终端连接已关闭');
}

window.addEventListener('beforeunload', disconnectProjectTerminal);
window.addEventListener('resize', resizeProjectTerminal);

// ── 顶部多任务链 Tab 系统 ─────────────────────────────────
// taskBelongsToProject 来自 shared/project-utils.js（全局）
let topbarTabs = [{ id: 'chat', kind: 'chat-home', label: 'AI 对话', closable: false }];
let activeTabId = 'chat';
let bottomTerminalTabs = [];
let activeBottomTerminalTabId = '';
let bottomTerminalTabSeq = 0;
let bottomTerminalDrawerInitialized = false;

function createBottomTerminalContext(tabId, projectName = '') {
  return {
    tabId,
    projectName,
    socket: null,
    terminal: null,
    fitAddon: null,
    connected: false,
    resizeTimer: null,
    lastState: 'closed',
    mountEl: null,
  };
}

let bottomTerminalContext = createBottomTerminalContext('', '');

// 初始化 Tab 系统
function initTopbarTabs() {
  renderTopbarTabs();
}

function scrollTabStrip(elementId, direction) {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.scrollBy({ left: direction * 220, behavior: 'smooth' });
}

function conversationTabId(convId) {
  return `conversation-${convId}`;
}

function conversationIdFromTabId(tabId) {
  return tabId.slice('conversation-'.length);
}

function compactTabLabel(text) {
  const value = String(text || '').trim() || '未命名任务链';
  return value.length > 18 ? value.slice(0, 17) + '…' : value;
}

function getConversationTabLabel(convId) {
  if (convId.startsWith('task-')) {
    const taskId = convId.replace('task-', '');
    const task = (tasksCache || []).find(t => t.id === taskId);
    return compactTabLabel(task?.prompt || '一次性任务');
  }
  const conv = (chatConversationsList || []).find(c => c.id === convId);
  return compactTabLabel(conv?.name || conversationsCache?.[convId] || convId);
}

function upsertConversationTab(convId) {
  const tabId = conversationTabId(convId);
  const label = getConversationTabLabel(convId);
  const existing = topbarTabs.find(t => t.id === tabId);
  if (existing) {
    existing.label = label;
    return tabId;
  }
  topbarTabs.push({
    id: tabId,
    kind: 'conversation',
    conversationId: convId,
    label,
    closable: true,
  });
  return tabId;
}

function syncConversationTabLabels() {
  topbarTabs.forEach(tab => {
    if (tab.kind === 'conversation' && tab.conversationId) {
      tab.label = getConversationTabLabel(tab.conversationId);
    }
  });
}

// 渲染 Tabs
function renderTopbarTabs() {
  const container = document.getElementById('topbar-tabs');
  if (!container) return;
  container.innerHTML = '';

  topbarTabs.forEach(tab => {
    const btn = document.createElement('button');
    btn.className = 'tab-item' + (activeTabId === tab.id ? ' active' : '');
    btn.type = 'button';
    btn.onclick = () => switchTab(tab.id);

    btn.title = tab.kind === 'conversation' ? tab.label : '';
    btn.innerHTML = `<span>${esc(tab.label)}</span>`;

    if (tab.closable) {
      const closeBtn = document.createElement('span');
      closeBtn.className = 'tab-close';
      closeBtn.innerHTML = '×';
      closeBtn.onclick = (e) => {
        e.stopPropagation();
        closeTab(tab.id);
      };
      btn.appendChild(closeBtn);
    }
    container.appendChild(btn);
  });
}

// 切换 Tab
function switchTab(tabId) {
  activeTabId = tabId;
  renderTopbarTabs();

  // 先隐藏所有 page
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));

  // 顶部 Tab 切换只负责任务开发区。
  const tabsEl = document.getElementById('topbar-tabs');
  if (tabsEl) tabsEl.style.display = '';

  if (tabId === 'chat') {
    stopChatFollow();
    activeConversationId = null;
    currentChatTaskId = null;
    currentPage = 'chat';
    document.getElementById('page-chat').classList.add('active');
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelector('[data-page="chat"]').classList.add('active');
    document.getElementById('page-title').textContent = '任务开发';
    loadConversations();
  } else if (tabId.startsWith('conversation-')) {
    stopChatFollow();
    activeConversationId = conversationIdFromTabId(tabId);
    currentPage = 'chat';
    document.getElementById('page-chat').classList.add('active');
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelector('[data-page="chat"]').classList.add('active');
    document.getElementById('page-title').textContent = '任务开发';
    loadConversations();
  }
}

// 关闭 Tab
function closeTab(tabId) {
  const idx = topbarTabs.findIndex(t => t.id === tabId);
  if (idx === -1) return;

  topbarTabs.splice(idx, 1);

  // 如果关闭的是当前激活的 Tab，则自动切回前一个 Tab 或者是 'chat'
  if (activeTabId === tabId) {
    stopChatFollow();
    const nextActiveId = topbarTabs[idx - 1] ? topbarTabs[idx - 1].id : 'chat';
    switchTab(nextActiveId);
  } else {
    renderTopbarTabs();
  }
}

// ── 底部项目终端抽屉 ─────────────────────────────────────
function initBottomTerminalDrawer() {
  if (bottomTerminalDrawerInitialized) return;
  bottomTerminalDrawerInitialized = true;

  const handle = document.getElementById('bottom-terminal-resize');
  const drawer = document.getElementById('bottom-terminal-drawer');
  if (!handle || !drawer) return;

  let dragging = false;
  handle.addEventListener('pointerdown', e => {
    dragging = true;
    handle.setPointerCapture?.(e.pointerId);
    document.body.style.userSelect = 'none';
  });
  handle.addEventListener('pointermove', e => {
    if (!dragging) return;
    const next = Math.min(window.innerHeight * 0.72, Math.max(220, window.innerHeight - e.clientY));
    drawer.style.height = `${Math.round(next)}px`;
    resizeBottomTerminal();
  });
  handle.addEventListener('pointerup', e => {
    dragging = false;
    handle.releasePointerCapture?.(e.pointerId);
    document.body.style.userSelect = '';
  });
}

function resolveCurrentTerminalProject() {
  if (currentPage === 'projects' && projectContext?.name) return projectContext.name;
  if (currentChatProjectName) return currentChatProjectName;
  if (chatNewSessionProject) return chatNewSessionProject;

  if (activeConversationId?.startsWith('task-')) {
    const taskId = activeConversationId.replace('task-', '');
    const task = (tasksCache || []).find(t => t.id === taskId);
    if (task?.project_name) return task.project_name;
    const project = (projectsCache || []).find(p => task && taskBelongsToProject(task, p));
    if (project) return project.name;
  } else if (activeConversationId) {
    const conv = (chatConversationsList || []).find(c => c.id === activeConversationId);
    if (conv?.project_name) return conv.project_name;
    if (conv?.project) return conv.project.split('/').pop();
  }
  return '';
}

async function refreshBottomTerminalProjects(preferredProject = '') {
  let projects = projectsCache || [];
  if (!projects.length) {
    projects = await fetch(`${API}/api/projects`).then(r => r.json()).catch(() => []);
    projectsCache = projects;
  }
}

function bottomTerminalTabLabel(projectName) {
  return projectName || '未选择项目';
}

function renderBottomTerminalTabs() {
  const el = document.getElementById('bottom-terminal-tabs');
  if (!el) return;
  el.innerHTML = bottomTerminalTabs.map(tab => `
    <button class="bottom-terminal-tab ${tab.id === activeBottomTerminalTabId ? 'active' : ''}" type="button" onclick="switchBottomTerminalTab('${tab.id}')" title="${esc(tab.projectName || '未选择项目')}">
      <span>▸</span>
      <span class="bottom-terminal-tab-label">${esc(tab.label)}</span>
      <span class="bottom-terminal-tab-close" onclick="event.stopPropagation(); closeBottomTerminalTab('${tab.id}')" title="关闭">×</span>
    </button>
  `).join('');
}

function createBottomTerminalTab(projectName = '') {
  const id = `term-${++bottomTerminalTabSeq}`;
  const tab = {
    id,
    projectName,
    label: bottomTerminalTabLabel(projectName),
    context: createBottomTerminalContext(id, projectName),
  };
  bottomTerminalTabs.push(tab);
  activateBottomTerminalTab(tab);
  return tab;
}

function getActiveBottomTerminalTab() {
  return bottomTerminalTabs.find(t => t.id === activeBottomTerminalTabId) || null;
}

function ensureBottomTerminalTab(projectName = '') {
  let tab = bottomTerminalTabs.find(t => t.projectName === projectName);
  if (!tab) tab = createBottomTerminalTab(projectName);
  activateBottomTerminalTab(tab);
  return tab;
}

function updateActiveBottomTerminalTabProject(projectName) {
  let tab = getActiveBottomTerminalTab();
  if (!tab) tab = createBottomTerminalTab(projectName);
  tab.projectName = projectName;
  tab.label = bottomTerminalTabLabel(projectName);
  tab.context.projectName = projectName;
  renderBottomTerminalTabs();
}

function activateBottomTerminalTab(tab) {
  if (!tab) return;
  if (!tab.context) tab.context = createBottomTerminalContext(tab.id, tab.projectName || '');
  activeBottomTerminalTabId = tab.id;
  bottomTerminalContext = tab.context;
  showActiveBottomTerminalMount();
  renderBottomTerminalTabs();
}

function showActiveBottomTerminalMount() {
  const mount = document.getElementById('bottom-terminal');
  if (!mount) return;
  Array.from(mount.children).forEach(child => {
    child.style.display = child.dataset.terminalTabId === activeBottomTerminalTabId ? 'block' : 'none';
  });
}

function addBottomTerminalTab() {
  const projectName = resolveCurrentTerminalProject() || '';
  const tab = createBottomTerminalTab(projectName);
  refreshBottomTerminalProjects(projectName);
  if (projectName) connectBottomTerminal(projectName);
  else {
    disconnectBottomTerminal({ context: tab.context });
    setBottomTerminalStatus('closed', '请选择项目后连接终端');
  }
}

function switchBottomTerminalTab(tabId) {
  const tab = bottomTerminalTabs.find(t => t.id === tabId);
  if (!tab) return;
  activateBottomTerminalTab(tab);
  refreshBottomTerminalProjects(tab.projectName);
  if (tab.context.socket || tab.context.terminal) {
    resizeBottomTerminal();
    tab.context.terminal?.focus();
  } else if (tab.projectName) {
    connectBottomTerminal(tab.projectName);
  }
  else {
    setBottomTerminalStatus('closed', '请选择项目后连接终端');
  }
}

function closeBottomTerminalTab(tabId) {
  const idx = bottomTerminalTabs.findIndex(t => t.id === tabId);
  if (idx === -1) return;
  const tab = bottomTerminalTabs[idx];
  const wasActive = activeBottomTerminalTabId === tabId;
  disconnectBottomTerminal({ context: tab.context });
  bottomTerminalTabs.splice(idx, 1);
  if (!bottomTerminalTabs.length) {
    activeBottomTerminalTabId = '';
    bottomTerminalContext = createBottomTerminalContext('', '');
    setBottomTerminalStatus('closed', '请选择项目后连接终端');
    renderBottomTerminalTabs();
    return;
  }
  if (wasActive) {
    activeBottomTerminalTabId = bottomTerminalTabs[Math.max(0, idx - 1)].id;
    switchBottomTerminalTab(activeBottomTerminalTabId);
  } else {
    renderBottomTerminalTabs();
  }
}

function setBottomTerminalStatus(state, message, context = bottomTerminalContext) {
  context.lastState = state;
}

function setBottomTerminalOpen(open) {
  const drawer = document.getElementById('bottom-terminal-drawer');
  const btn = document.getElementById('top-terminal-btn');
  if (drawer) drawer.style.display = open ? 'flex' : 'none';
  if (btn) btn.classList.toggle('active', open);
}

function isBottomTerminalOpen() {
  const drawer = document.getElementById('bottom-terminal-drawer');
  return !!drawer && drawer.style.display !== 'none';
}

async function syncBottomTerminalProjectFromContext() {
  if (!isBottomTerminalOpen()) return;
  const projectName = resolveCurrentTerminalProject();
  await refreshBottomTerminalProjects(projectName);
  const activeTab = getActiveBottomTerminalTab();
  if (projectName && activeTab && projectName !== activeTab.projectName) {
    let tab = bottomTerminalTabs.find(t => t.projectName === projectName);
    if (!tab) tab = createBottomTerminalTab(projectName);
    activateBottomTerminalTab(tab);
    if (!tab.context.socket && !tab.context.terminal) connectBottomTerminal(projectName);
  }
}

async function toggleBottomTerminal() {
  const drawer = document.getElementById('bottom-terminal-drawer');
  const isOpen = drawer && drawer.style.display !== 'none';
  if (isOpen) {
    closeBottomTerminal();
    return;
  }

  initBottomTerminalDrawer();
  setBottomTerminalOpen(true);
  const projectName = resolveCurrentTerminalProject();
  await refreshBottomTerminalProjects(projectName);
  ensureBottomTerminalTab(projectName);
  if (projectName) {
    connectBottomTerminal(projectName);
  } else {
    setBottomTerminalStatus('closed', '请选择项目后连接终端');
  }
  setTimeout(resizeBottomTerminal, 60);
}

function closeBottomTerminal(disconnect = false) {
  setBottomTerminalOpen(false);
  if (disconnect) {
    disconnectAllBottomTerminals();
    bottomTerminalContext = createBottomTerminalContext('', '');
    bottomTerminalTabs = [];
    activeBottomTerminalTabId = '';
    renderBottomTerminalTabs();
    setBottomTerminalStatus('closed', '终端连接已关闭');
  }
}

function ensureBottomTerminalMounted() {
  const mount = document.getElementById('bottom-terminal');
  if (!mount || !window.Terminal || !window.FitAddon) return false;
  const context = bottomTerminalContext;
  if (context.terminal && context.mountEl) {
    showActiveBottomTerminalMount();
    return true;
  }

  const pane = document.createElement('div');
  pane.className = 'bottom-terminal-pane';
  pane.dataset.terminalTabId = activeBottomTerminalTabId;
  mount.appendChild(pane);
  context.mountEl = pane;
  showActiveBottomTerminalMount();
  const { terminal, fitAddon } = createEnhancedTerminal(pane);
  context.terminal = terminal;
  context.fitAddon = fitAddon;
  context.terminal.onData(data => {
    const socket = context.socket;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'input', data }));
    }
  });
  resizeBottomTerminal();
  return true;
}

function connectBottomTerminal(projectName) {
  const context = bottomTerminalContext;
  if (!projectName) {
    disconnectBottomTerminal({ context });
    context.projectName = '';
    setBottomTerminalStatus('closed', '请选择项目后连接终端');
    return;
  }
  setBottomTerminalOpen(true);
  initBottomTerminalDrawer();
  if (context.projectName === projectName && context.socket &&
      (context.socket.readyState === WebSocket.OPEN || context.socket.readyState === WebSocket.CONNECTING)) {
    resizeBottomTerminal();
    context.terminal?.focus();
    return;
  }
  disconnectBottomTerminal({ dispose: false, context });
  context.projectName = projectName;
  updateActiveBottomTerminalTabProject(projectName);

  if (!ensureBottomTerminalMounted()) {
    setBottomTerminalStatus('error', '终端资源未加载，请刷新页面后重试');
    return;
  }

  setBottomTerminalStatus('closed', '正在连接项目容器...');
  context.terminal.clear();
  context.terminal.writeln(`Connecting to ${projectName}...`);

  const socket = new WebSocket(terminalWsUrl(projectName));
  context.socket = socket;
  socket.addEventListener('open', () => {
    if (context.socket !== socket) return;
    context.connected = true;
    resizeBottomTerminal();
  });
  socket.addEventListener('message', event => {
    if (context.socket !== socket) return;
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.type === 'output') {
      context.terminal.write(String(message.data || ''));
    } else if (message.type === 'status') {
      context.connected = message.state === 'connected';
      setBottomTerminalStatus(message.state, message.message || '', context);
      if (message.state === 'error') {
        context.terminal.writeln(`\r\n${message.message || '连接失败'}`);
      }
    }
  });
  socket.addEventListener('close', () => {
    if (context.socket !== socket) return;
    context.connected = false;
    if (context.lastState !== 'error') {
      setBottomTerminalStatus('closed', '终端连接已关闭', context);
    }
  });
  socket.addEventListener('error', () => {
    if (context.socket !== socket) return;
    context.connected = false;
    setBottomTerminalStatus('error', '终端连接失败', context);
  });
}

function reconnectBottomTerminal() {
  const projectName = bottomTerminalContext.projectName
    || resolveCurrentTerminalProject();
  disconnectBottomTerminal({ dispose: false });
  connectBottomTerminal(projectName);
}

function resizeBottomTerminal() {
  const context = bottomTerminalContext;
  if (!context.terminal || !context.fitAddon) return;
  clearTimeout(context.resizeTimer);
  context.resizeTimer = setTimeout(() => {
    try {
      context.fitAddon.fit();
      const socket = context.socket;
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({
          type: 'resize',
          cols: context.terminal.cols,
          rows: context.terminal.rows,
        }));
      }
    } catch {}
  }, 30);
}

function disconnectBottomTerminal(options = {}) {
  const { dispose = true, context = bottomTerminalContext } = options;
  const socket = context.socket;
  context.socket = null;
  context.connected = false;
  if (socket && socket.readyState !== WebSocket.CLOSED) {
    socket.close();
  }
  if (dispose && context.terminal) {
    context.terminal.dispose();
    context.terminal = null;
    context.fitAddon = null;
    if (context.mountEl) {
      context.mountEl.remove();
      context.mountEl = null;
    }
  }
  clearTimeout(context.resizeTimer);
}

function disconnectAllBottomTerminals() {
  bottomTerminalTabs.forEach(tab => {
    if (tab.context) disconnectBottomTerminal({ context: tab.context });
  });
}
