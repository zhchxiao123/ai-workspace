// ── 项目工作台 ────────────────────────────────────────────
// 项目归属判断函数已统一迁移至 js/shared/project-utils.js
// （脚本在 utils 之后、projects 之前加载，保证全局可用）

async function loadProjectsDashboard() {
  try {
    const [projects, tasks, accounts] = await Promise.all([
      fetch(`${API}/api/projects`).then(r => r.json()).catch(() => []),
      fetch(`${API}/api/tasks?limit=100`).then(r => r.json()).catch(() => []),
      fetch(`${API}/api/accounts`).then(r => r.json()).catch(() => []),
    ]);
    projectsCache = projects;
    projectDashboardData = { tasks, accounts };
    renderProjectCards(projects, tasks, accounts);
    if (projectContext) {
      const current = projects.find(p => p.name === projectContext.name);
      current ? renderProjectDetail(current) : backToProjects();
    }
  } catch (e) {
    document.getElementById('project-grid').innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

function renderProjectCards(projects, tasks, accounts) {
  const grid = document.getElementById('project-grid');
  if (!projects.length) {
    grid.innerHTML = `<div class="empty">暂无项目配置</div>`;
    return;
  }
  const accountMap = new Map(accounts.map(a => [a.name, a]));
  grid.innerHTML = projects.map(project => {
    const projectTasks = tasks.filter(t => taskBelongsToProject(t, project));
    const running = projectTasks.filter(t => t.status === 'running').length;
    const failed  = projectTasks.filter(t => t.status === 'failed').length;
    const latest  = [...projectTasks].sort((a, b) => new Date(b.created || 0) - new Date(a.created || 0))[0];
    const account = accountMap.get(project.account);
    const accountBadge = account
      ? `<span class="badge ${account.type}">${account.type}</span><span class="badge proxy-${account.proxy || 'relay'}">proxy: ${esc(account.proxy || 'relay')}</span>`
      : `<span class="badge offline">账号缺失</span>`;
    let latestText = '还没有任务记录';
    if (latest && latest.prompt) {
      const cleanPrompt = String(latest.prompt || '').replace(/\s+/g, ' ');
      const maxLen = 100;
      const truncated = cleanPrompt.length > maxLen ? cleanPrompt.substring(0, maxLen) + '...' : cleanPrompt;
      latestText = `最近：${esc(truncated)}`;
    }
    return `<div class="project-card" onclick="openProject('${esc(project.name)}')">
  <div class="project-head">
    <div style="min-width:0">
      <div class="project-title">${esc(project.name)}</div>
      <div class="project-path" title="${esc(project.path)}">${esc(project.path)}</div>
    </div>
  </div>
  <div class="account-badges" style="margin-top:10px">${accountBadge}<span class="chip">${esc(project.account)}</span></div>
  <div class="project-stats">
    <div class="project-stat"><div class="account-stat-label">总任务</div><div class="account-stat-value">${projectTasks.length}</div></div>
    <div class="project-stat"><div class="account-stat-label">运行中</div><div class="account-stat-value">${running}</div></div>
    <div class="project-stat"><div class="account-stat-label">完成</div><div class="account-stat-value">${projectTasks.filter(t => t.status === 'done').length}</div></div>
    <div class="project-stat"><div class="account-stat-label">失败</div><div class="account-stat-value">${failed}</div></div>
  </div>
  <div class="account-meta" style="margin-top:10px" ${latest ? `title="${esc(latest.prompt)}"` : ''}>${latestText}</div>
  <div class="account-footer" style="margin-top:10px">
    <button class="btn" style="font-size:12px" onclick="event.stopPropagation();openProjectFormModal('${esc(project.name)}')">编辑</button>
    <button class="btn danger" style="font-size:12px" onclick="event.stopPropagation();deleteProjectConfirm('${esc(project.name)}')">删除</button>
  </div>
</div>`;
  }).join('');
}

async function openProject(name) {
  const project = projectsCache.find(p => p.name === name);
  if (!project) return;
  projectContext = project;
  document.getElementById('project-list-view').style.display = 'none';
  document.getElementById('project-detail-view').style.display = '';
  document.getElementById('page-title').textContent = `项目 · ${project.name}`;
  await loadProjectsDashboard();
}

function backToProjects() {
  disconnectProjectTerminal();
  projectContext = null;
  document.getElementById('project-detail-view').style.display = 'none';
  document.getElementById('project-list-view').style.display = '';
  document.getElementById('page-title').textContent = '项目管理';
}

function renderProjectDetail(project) {
  const { tasks, accounts } = projectDashboardData;
  const account = accounts.find(a => a.name === project.account);
  const projectTasks = tasks.filter(t => taskBelongsToProject(t, project));
  const running = projectTasks.filter(t => t.status === 'running').length;
  const done    = projectTasks.filter(t => t.status === 'done').length;
  const failed  = projectTasks.filter(t => t.status === 'failed').length;

  document.getElementById('project-detail-summary').innerHTML = `
<div class="project-title">${esc(project.name)}</div>
<div class="project-path" title="${esc(project.path)}">${esc(project.path)}</div>
<div class="account-badges" style="margin-top:12px">
  <span class="chip">账号 ${esc(project.account)}</span>
  ${account ? `<span class="badge ${account.type}">${account.type}</span><span class="badge proxy-${account.proxy || 'relay'}">proxy: ${esc(account.proxy || 'relay')}</span>` : `<span class="badge offline">账号缺失</span>`}
</div>
<div class="project-stats" style="margin-top:16px">
  <div class="project-stat"><div class="account-stat-label">总任务</div><div class="account-stat-value">${projectTasks.length}</div></div>
  <div class="project-stat"><div class="account-stat-label">运行中</div><div class="account-stat-value" style="${running > 0 ? 'color:var(--green)' : ''}">${running}</div></div>
  <div class="project-stat"><div class="account-stat-label">完成</div><div class="account-stat-value">${done}</div></div>
  <div class="project-stat"><div class="account-stat-label">失败</div><div class="account-stat-value" style="${failed > 0 ? 'color:var(--red)' : ''}">${failed}</div></div>
</div>`;

  updateTerminalWarning();
}

function submitForCurrentProject() {
  if (!projectContext) return;
  startNewChat({ projectName: projectContext.name });
}

// ── 项目终端 ──────────────────────────────────────────────
function terminalWsUrl(projectName) {
  return wsUrl(`${API}/api/projects/${encodeURIComponent(projectName)}/terminal`);
}

function setTerminalStatus(state, message) {
  terminalContext.lastState = state;
  const dot    = document.getElementById('project-terminal-dot');
  const status = document.getElementById('project-terminal-status');
  if (dot) {
    const cls = state === 'connected' ? 'running' : state === 'error' ? 'failed' : 'killed';
    dot.className = `status-dot ${cls}`;
    dot.textContent = state === 'connected' ? '已连接' : state === 'error' ? '错误' : '未连接';
  }
  if (status) status.textContent = message || '';
}

function updateTerminalWarning() {
  const warning = document.getElementById('project-terminal-warning');
  if (!warning || !projectContext) return;
  const account = projectDashboardData.accounts.find(a => a.name === projectContext.account);
  if (account?.busy) {
    warning.textContent = `账号 ${account.name} 当前有运行中的调度任务，终端仍可使用。`;
    warning.classList.add('active');
  } else {
    warning.textContent = '';
    warning.classList.remove('active');
  }
}

// ── 项目 CRUD ─────────────────────────────────────────────
let _editingProjectName = null;

async function openProjectFormModal(name) {
  _editingProjectName = name || null;
  document.getElementById('project-form-title').textContent = name ? `编辑项目 · ${name}` : '新建项目';
  document.getElementById('project-form-msg').style.display = 'none';

  const nameInput = document.getElementById('project-form-name');
  nameInput.disabled = !!name;

  // 填充账号下拉
  const accounts = (await fetch(`${API}/api/accounts`).then(r => r.json()).catch(() => []));
  const sel = document.getElementById('project-form-account');
  sel.innerHTML = accounts.map(a =>
    `<option value="${esc(a.name)}">${esc(a.name)} (${a.type})</option>`
  ).join('');

  if (name) {
    const project = (projectsCache || []).find(p => p.name === name);
    if (project) {
      nameInput.value = project.name;
      sel.value = project.account;
      document.getElementById('project-form-path').value = project.path;
    }
  } else {
    nameInput.value = '';
    sel.value = accounts[0]?.name || '';
    document.getElementById('project-form-path').value = '';
  }

  document.getElementById('project-form-modal').style.display = '';
}

function closeProjectFormModal(event) {
  if (event && event.target !== event.currentTarget) return;
  document.getElementById('project-form-modal').style.display = 'none';
}

async function saveProjectForm() {
  const name    = _editingProjectName || document.getElementById('project-form-name').value.trim();
  const account = document.getElementById('project-form-account').value;
  const path    = document.getElementById('project-form-path').value.trim();
  if (!name)    { showProjectFormMsg('请填写项目名', 'error'); return; }
  if (!account) { showProjectFormMsg('请选择账号',   'error'); return; }
  if (!path)    { showProjectFormMsg('请填写路径',   'error'); return; }

  const btn = document.getElementById('project-form-btn');
  btn.disabled = true;
  try {
    const isEdit = !!_editingProjectName;
    const url    = isEdit ? `${API}/api/projects/${encodeURIComponent(name)}` : `${API}/api/projects`;
    const r = await fetch(url, {
      method: isEdit ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(isEdit ? { account, path } : { name, account, path }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { showProjectFormMsg(data.detail || '保存失败', 'error'); return; }
    closeProjectFormModal();
    await loadProjectsDashboard();
  } catch (e) {
    showProjectFormMsg(e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

async function deleteProjectConfirm(name) {
  if (!confirm(`确认删除项目「${name}」？容器不会自动销毁，配置从 projects.conf 中移除。`)) return;
  try {
    const r = await fetch(`${API}/api/projects/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      alert(d.detail || '删除失败');
      return;
    }
    if (projectContext?.name === name) backToProjects();
    else await loadProjectsDashboard();
  } catch (e) {
    alert(e.message);
  }
}

function showProjectFormMsg(text, type) {
  const el = document.getElementById('project-form-msg');
  el.textContent = text;
  el.className = type === 'error' ? 'inline-alert' : '';
  el.style.display = '';
}

function openProjectTerminal() {
  if (!projectContext) return;
  updateTerminalWarning();
  if (terminalContext.projectName !== projectContext.name) {
    disconnectProjectTerminal();
  }
  terminalContext.projectName = projectContext.name;
  if (!terminalContext.socket || terminalContext.socket.readyState === WebSocket.CLOSED) {
    connectProjectTerminal();
  } else {
    resizeProjectTerminal();
  }
}

function ensureTerminalMounted() {
  const mount = document.getElementById('project-terminal');
  if (!mount || !window.Terminal || !window.FitAddon) return false;
  if (terminalContext.terminal && mount.childElementCount) return true;

  mount.innerHTML = '';
  const { terminal, fitAddon } = createEnhancedTerminal(mount);
  terminalContext.terminal = terminal;
  terminalContext.fitAddon = fitAddon;

  terminalContext.terminal.onData(data => {
    const socket = terminalContext.socket;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'input', data }));
    }
  });
  resizeProjectTerminal();
  return true;
}

function connectProjectTerminal() {
  if (!projectContext) return;
  disconnectProjectTerminal();
  terminalContext.projectName = projectContext.name;
  if (!ensureTerminalMounted()) {
    setTerminalStatus('error', '终端资源未加载，请刷新页面后重试');
    return;
  }
  updateTerminalWarning();
  setTerminalStatus('closed', '正在连接项目容器...');
  terminalContext.terminal.clear();
  terminalContext.terminal.writeln(`Connecting to ${projectContext.name}...`);

  const socket = new WebSocket(terminalWsUrl(projectContext.name));
  terminalContext.socket = socket;

  socket.addEventListener('open', () => {
    terminalContext.connected = true;
    resizeProjectTerminal();
  });
  socket.addEventListener('message', event => {
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.type === 'output') {
      terminalContext.terminal.write(String(message.data || ''));
    } else if (message.type === 'status') {
      terminalContext.connected = message.state === 'connected';
      setTerminalStatus(message.state, message.message || '');
      if (message.state === 'error') terminalContext.terminal.writeln(`\r\n${message.message || '连接失败'}`);
    }
  });
  socket.addEventListener('close', () => {
    terminalContext.connected = false;
    if (terminalContext.lastState !== 'error') {
      setTerminalStatus('closed', '终端连接已关闭');
    }
  });
  socket.addEventListener('error', () => {
    terminalContext.connected = false;
    setTerminalStatus('error', '终端连接失败');
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
    } catch { }
  }, 30);
}

function disconnectProjectTerminal() {
  const socket = terminalContext.socket;
  terminalContext.socket    = null;
  terminalContext.connected = false;
  if (socket && socket.readyState !== WebSocket.CLOSED) {
    socket.close();
  }
  if (terminalContext.terminal) {
    terminalContext.terminal.dispose();
  }
  terminalContext.terminal   = null;
  terminalContext.fitAddon   = null;
  terminalContext.projectName = '';
  terminalContext.lastState   = 'closed';
  clearTimeout(terminalContext.resizeTimer);
  setTerminalStatus('closed', '终端连接已关闭');
}
