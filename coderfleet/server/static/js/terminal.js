// ── 顶部多 Tab 终端系统 JS 实现 ──
// taskBelongsToProject 来自 shared/project-utils.js（全局）
let topbarTabs = [{ id: 'chat', label: '📁 AI 对话', closable: false }];
let activeTabId = 'chat';
let multiTerminalContexts = {}; // projectName -> { terminal, fitAddon, socket, connected }

// 初始化 Tab 系统
function initTopbarTabs() {
  renderTopbarTabs();
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

  // 先隐藏所有 page 和多终端容器
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.multi-term-container').forEach(c => c.classList.remove('active'));

  // 显示顶级 Tab 状态下的 topbar-tabs 和 + 按钮
  const tabsEl = document.getElementById('topbar-tabs');
  const addBtn = document.getElementById('add-tab-btn');
  if (tabsEl) tabsEl.style.display = '';
  if (addBtn) addBtn.style.display = '';

  if (tabId === 'chat') {
    currentPage = 'chat';
    document.getElementById('page-chat').classList.add('active');
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelector('[data-page="chat"]').classList.add('active');
    document.getElementById('page-title').textContent = '任务开发';
  } else if (tabId.startsWith('terminal-')) {
    currentPage = 'terminal';
    const projectName = tabId.replace('terminal-', '');
    document.getElementById('page-tab-terminal').classList.add('active');

    // 激活对应的终端容器
    const termContainer = document.getElementById(`term-container-${projectName}`);
    if (termContainer) {
      termContainer.classList.add('active');
    }

    // 连接或重置该项目的终端
    openMultiTerminal(projectName);
  }
}

// 关闭 Tab
function closeTab(tabId) {
  const idx = topbarTabs.findIndex(t => t.id === tabId);
  if (idx === -1) return;

  const projectName = tabId.replace('terminal-', '');
  // 断开该终端的 websocket
  disconnectMultiTerminal(projectName);

  // 移除 DOM 容器
  const termContainer = document.getElementById(`term-container-${projectName}`);
  if (termContainer) termContainer.remove();

  topbarTabs.splice(idx, 1);

  // 如果关闭的是当前激活的 Tab，则自动切回前一个 Tab 或者是 'chat'
  if (activeTabId === tabId) {
    const nextActiveId = topbarTabs[idx - 1] ? topbarTabs[idx - 1].id : 'chat';
    switchTab(nextActiveId);
  } else {
    renderTopbarTabs();
  }
}

// 显示添加 Tab 的项目弹出菜单
function showAddTabMenu(event) {
  event.stopPropagation();
  // 移除可能存在的旧菜单
  const oldMenu = document.querySelector('.add-tab-menu');
  if (oldMenu) oldMenu.remove();

  const menu = document.createElement('div');
  menu.className = 'add-tab-menu';

  // 计算定位
  const rect = event.currentTarget.getBoundingClientRect();
  menu.style.left = `${rect.left}px`;
  menu.style.top = `${rect.bottom + window.scrollY + 6}px`;

  // 获取当前所有的项目，展示为菜单项
  const projects = projectDashboardData?.projects || [];
  if (projects.length === 0) {
    menu.innerHTML = `<div class="add-tab-menu-header">暂无项目</div>`;
  } else {
    menu.innerHTML = `<div class="add-tab-menu-header">连接项目容器终端</div>`;
    projects.forEach(p => {
      const btn = document.createElement('button');
      btn.className = 'add-tab-menu-item';
      btn.innerHTML = `💻 终端: ${esc(p.name)}`;
      btn.onclick = () => {
        addTerminalTab(p.name);
        menu.remove();
      };
      menu.appendChild(btn);
    });
  }

  document.body.appendChild(menu);

  // 点击外部时关闭菜单
  const closeMenu = (e) => {
    if (!menu.contains(e.target) && e.target !== event.currentTarget) {
      menu.remove();
      document.removeEventListener('click', closeMenu);
    }
  };
  setTimeout(() => document.addEventListener('click', closeMenu), 0);
}

// 新增终端 Tab
function addTerminalTab(projectName) {
  const tabId = `terminal-${projectName}`;
  const exists = topbarTabs.some(t => t.id === tabId);

  if (!exists) {
    topbarTabs.push({
      id: tabId,
      label: `💻 终端: ${projectName}`,
      closable: true
    });

    // 创建对应的终端容器
    const pageContainer = document.getElementById('page-tab-terminal');
    const termContainer = document.createElement('div');
    termContainer.className = 'multi-term-container';
    termContainer.id = `term-container-${projectName}`;
    termContainer.innerHTML = `
      <div class="terminal-card" style="height: 100%; border: none;">
        <div class="terminal-toolbar">
          <div class="terminal-status">
            <span class="status-dot killed" id="term-dot-${projectName}">未连接</span>
            <span class="terminal-status-text" id="term-status-${projectName}">点击或切换到此 Tab 后连接项目容器</span>
          </div>
          <button class="btn" type="button" onclick="reconnectMultiTerminal('${projectName}')">重新连接</button>
        </div>
        <div class="terminal-warning" id="term-warning-${projectName}"></div>
        <div class="terminal-mount" id="term-mount-${projectName}" style="flex: 1; height: calc(100% - 50px);"></div>
      </div>
    `;
    pageContainer.appendChild(termContainer);
  }

  switchTab(tabId);
}

// ── 多终端实例化和 WebSocket 连接逻辑 ──
function openMultiTerminal(projectName) {
  updateMultiTerminalWarning(projectName);
  let ctx = multiTerminalContexts[projectName];
  if (!ctx) {
    ctx = {
      terminal: null,
      fitAddon: null,
      socket: null,
      connected: false
    };
    multiTerminalContexts[projectName] = ctx;
  }

  if (!ctx.socket || ctx.socket.readyState === WebSocket.CLOSED) {
    connectMultiTerminal(projectName);
  } else {
    resizeMultiTerminal(projectName);
  }
}

function ensureMultiTerminalMounted(projectName) {
  const mount = document.getElementById(`term-mount-${projectName}`);
  if (!mount || !window.Terminal || !window.FitAddon) return false;

  let ctx = multiTerminalContexts[projectName];
  if (ctx && ctx.terminal && mount.childElementCount) return true;

  mount.innerHTML = '';
  const { terminal, fitAddon } = createEnhancedTerminal(mount);
  ctx.terminal = terminal;
  ctx.fitAddon  = fitAddon;

  ctx.terminal.onData(data => {
    if (ctx.socket && ctx.socket.readyState === WebSocket.OPEN) {
      ctx.socket.send(JSON.stringify({ type: 'input', data }));
    }
  });

  // 窗口大小变化时自适应
  window.removeEventListener('resize', ctx.resizeHandler);
  ctx.resizeHandler = () => resizeMultiTerminal(projectName);
  window.addEventListener('resize', ctx.resizeHandler);

  resizeMultiTerminal(projectName);
  return true;
}

function connectMultiTerminal(projectName) {
  disconnectMultiTerminal(projectName);

  let ctx = multiTerminalContexts[projectName];
  if (!ctx) {
    ctx = { terminal: null, fitAddon: null, socket: null, connected: false };
    multiTerminalContexts[projectName] = ctx;
  }

  if (!ensureMultiTerminalMounted(projectName)) {
    setMultiTerminalStatus(projectName, 'error', '终端资源未加载，请刷新页面后重试');
    return;
  }

  updateMultiTerminalWarning(projectName);
  setMultiTerminalStatus(projectName, 'closed', '正在连接项目容器...');
  ctx.terminal.clear();
  ctx.terminal.writeln(`Connecting to terminal of project container [${projectName}]...`);

  const socket = new WebSocket(terminalWsUrl(projectName));
  ctx.socket = socket;

  socket.addEventListener('open', () => {
    ctx.connected = true;
    resizeMultiTerminal(projectName);
  });

  socket.addEventListener('message', (e) => {
    const d = JSON.parse(e.data);
    if (d.type === 'output') {
      ctx.terminal.write(d.data);
    } else if (d.type === 'status') {
      setMultiTerminalStatus(projectName, d.status, d.message);
    }
  });

  socket.addEventListener('close', () => {
    ctx.connected = false;
    setMultiTerminalStatus(projectName, 'closed', '连接已断开');
  });

  socket.addEventListener('error', () => {
    ctx.connected = false;
    setMultiTerminalStatus(projectName, 'error', '连接出错');
  });
}

function disconnectMultiTerminal(projectName) {
  const ctx = multiTerminalContexts[projectName];
  if (!ctx) return;
  if (ctx.socket) {
    ctx.socket.close();
    ctx.socket = null;
  }
  ctx.connected = false;
  setMultiTerminalStatus(projectName, 'closed', '未连接');
}

function reconnectMultiTerminal(projectName) {
  connectMultiTerminal(projectName);
}

function resizeMultiTerminal(projectName) {
  const ctx = multiTerminalContexts[projectName];
  if (!ctx || !ctx.terminal || !ctx.fitAddon) return;

  // 延迟确保 DOM 已经完全渲染
  setTimeout(() => {
    try {
      ctx.fitAddon.fit();
      const dims = ctx.fitAddon.proposeDimensions();
      if (dims && ctx.socket && ctx.socket.readyState === WebSocket.OPEN) {
        ctx.socket.send(JSON.stringify({
          type: 'resize',
          cols: dims.cols,
          rows: dims.rows
        }));
      }
    } catch (e) {
      console.warn('fit xterm error', e);
    }
  }, 50);
}

function setMultiTerminalStatus(projectName, status, message) {
  const dot = document.getElementById(`term-dot-${projectName}`);
  const txt = document.getElementById(`term-status-${projectName}`);
  if (!dot || !txt) return;

  dot.className = 'status-dot ' + (status === 'connected' ? 'running' : 'killed');
  dot.textContent = status === 'connected' ? '已连接' : (status === 'error' ? '错误' : '未连接');
  txt.textContent = message;
}

function updateMultiTerminalWarning(projectName) {
  const warning = document.getElementById(`term-warning-${projectName}`);
  if (!warning) return;

  // 可以获取当前项目下是否有正在运行的任务
  const running = projectDashboardData?.tasks?.some(t => taskBelongsToProject(t, { name: projectName }) && t.status === 'running');
  if (running) {
    warning.textContent = '警告：当前项目有后台任务正在运行，在终端手动执行命令可能会干扰其运行。';
    warning.classList.add('active');
  } else {
    warning.textContent = '';
    warning.classList.remove('active');
  }
}
