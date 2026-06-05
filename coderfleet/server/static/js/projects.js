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
    const ideBadge = project.ide_enabled
      ? `<span class="badge running">IDE ${esc(project.ide_port || '')}</span>`
      : '';
    const activeBadge = project.active === false
      ? `<span class="badge offline">容器停用</span>`
      : '';
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
  <div class="account-badges" style="margin-top:10px">${accountBadge}${ideBadge}${activeBadge}<span class="chip">${esc(project.account)}</span></div>
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
  await Promise.all([loadProjectsDashboard(), loadProjectEnvVars(name)]);
}

function backToProjects() {
  const frame = document.getElementById('project-ide-frame');
  if (frame) frame.src = 'about:blank';
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
  ${project.ide_enabled ? `<span class="badge running">IDE ${esc(project.ide_port || '')}</span>` : ''}
</div>
<div class="project-stats" style="margin-top:16px">
  <div class="project-stat"><div class="account-stat-label">总任务</div><div class="account-stat-value">${projectTasks.length}</div></div>
  <div class="project-stat"><div class="account-stat-label">运行中</div><div class="account-stat-value" style="${running > 0 ? 'color:var(--green)' : ''}">${running}</div></div>
  <div class="project-stat"><div class="account-stat-label">完成</div><div class="account-stat-value">${done}</div></div>
  <div class="project-stat"><div class="account-stat-label">失败</div><div class="account-stat-value" style="${failed > 0 ? 'color:var(--red)' : ''}">${failed}</div></div>
</div>`;

  renderProjectIde(project);
}

function projectIdeUrl(project) {
  if (!project?.ide_enabled || !project.ide_port) return '';
  if (project.ide_url) return project.ide_url;
  let host = window.location.hostname || '127.0.0.1';
  if (host.includes(':') && !host.startsWith('[')) host = `[${host}]`;
  return `http://${host}:${project.ide_port}`;
}

function renderProjectIde(project) {
  const frame = document.getElementById('project-ide-frame');
  const dot = document.getElementById('project-ide-dot');
  const status = document.getElementById('project-ide-status');
  const openBtn = document.getElementById('project-ide-open');
  if (!frame || !dot || !status || !openBtn) return;

  const url = projectIdeUrl(project);
  if (!url) {
    dot.className = 'status-dot killed';
    dot.textContent = '未启用';
    status.textContent = '在项目配置中启用 IDE 后可浏览代码';
    openBtn.style.display = 'none';
    frame.dataset.projectName = '';
    frame.dataset.ideUrl = '';
    frame.src = 'about:blank';
    return;
  }

  dot.className = 'status-dot running';
  dot.textContent = 'IDE';
  status.textContent = url;
  openBtn.style.display = '';
  if (frame.dataset.ideUrl !== url || frame.dataset.projectName !== project.name) {
    frame.dataset.projectName = project.name;
    frame.dataset.ideUrl = url;
    frame.src = url;
  }
}

function openProjectIdeWindow() {
  const url = projectIdeUrl(projectContext);
  if (url) window.open(url, '_blank', 'noopener');
}

function submitForCurrentProject() {
  if (!projectContext) return;
  startNewChat({ projectName: projectContext.name });
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
      document.getElementById('project-form-active').checked = project.active !== false;
      document.getElementById('project-form-ide-enabled').checked = !!project.ide_enabled;
      document.getElementById('project-form-ide-port').value = project.ide_port || '';
      document.getElementById('project-form-ide-auth').value = project.ide_auth || 'none';
      document.getElementById('project-form-ide-remote').checked = !!project.ide_remote;
    }
  } else {
    nameInput.value = '';
    sel.value = accounts[0]?.name || '';
    document.getElementById('project-form-path').value = '';
    document.getElementById('project-form-active').checked = true;
    document.getElementById('project-form-ide-enabled').checked = false;
    document.getElementById('project-form-ide-port').value = '';
    document.getElementById('project-form-ide-auth').value = 'none';
    document.getElementById('project-form-ide-remote').checked = false;
  }
  toggleProjectIdePort();

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
  const active  = document.getElementById('project-form-active').checked;
  const ideEnabled = document.getElementById('project-form-ide-enabled').checked;
  const idePortRaw = document.getElementById('project-form-ide-port').value.trim();
  const idePort = idePortRaw ? Number(idePortRaw) : null;
  const ideAuth   = document.getElementById('project-form-ide-auth').value || 'none';
  const ideRemote = document.getElementById('project-form-ide-remote').checked;
  if (!name)    { showProjectFormMsg('请填写项目名', 'error'); return; }
  if (!account) { showProjectFormMsg('请选择账号',   'error'); return; }
  if (!path)    { showProjectFormMsg('请填写路径',   'error'); return; }
  if (ideEnabled && idePort !== null && (!Number.isInteger(idePort) || idePort < 1024 || idePort > 65535)) {
    showProjectFormMsg('IDE 端口需为 1024-65535 之间的整数，或留空自动分配', 'error');
    return;
  }

  const btn = document.getElementById('project-form-btn');
  btn.disabled = true;
  try {
    const isEdit = !!_editingProjectName;
    const url    = isEdit ? `${API}/api/projects/${encodeURIComponent(name)}` : `${API}/api/projects`;
    const r = await fetch(url, {
      method: isEdit ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(isEdit
        ? { account, path, active, ide_enabled: ideEnabled, ide_port: idePort, ide_auth: ideAuth, ide_remote: ideRemote }
        : { name, account, path, active, ide_enabled: ideEnabled, ide_port: idePort, ide_auth: ideAuth, ide_remote: ideRemote }),
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

function toggleProjectIdePort() {
  const enabled = document.getElementById('project-form-ide-enabled')?.checked;
  const wrap = document.getElementById('project-form-ide-port-wrap');
  if (wrap) wrap.style.display = enabled ? 'flex' : 'none';
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

// ── 项目环境变量 ──────────────────────────────────────────

async function loadProjectEnvVars(name) {
  try {
    const resp = await fetch(`${API}/api/projects/${encodeURIComponent(name)}/env`).then(r => r.json());
    renderProjectEnvRows(resp.vars || {});
  } catch {
    renderProjectEnvRows({});
  }
}

function renderProjectEnvRows(vars) {
  const container = document.getElementById('project-env-rows');
  if (!container) return;
  const entries = Object.entries(vars);
  if (!entries.length) {
    container.innerHTML = '<div style="color:var(--text-3);font-size:12px;padding:2px 0">暂无变量</div>';
    return;
  }
  container.innerHTML = entries.map(([k, v]) => `
    <div class="env-row" id="proj-env-row-${esc(k)}">
      <span class="env-key">${esc(k)}</span>
      <input class="env-val-input" id="proj-env-val-${esc(k)}" type="text" value="${esc(v)}" autocomplete="off" spellcheck="false">
      <button class="btn danger" style="font-size:11px;padding:2px 8px" onclick="removeProjectEnvRow('${esc(k)}')">删除</button>
    </div>`).join('');
}

function addProjectEnvRow() {
  const k = document.getElementById('project-new-env-key').value.trim();
  const v = document.getElementById('project-new-env-val').value;
  if (!k) return;
  const container = document.getElementById('project-env-rows');
  const empty = container.querySelector('div[style*="color:var(--text-3)"]');
  if (empty) empty.remove();
  document.getElementById(`proj-env-row-${k}`)?.remove();
  container.insertAdjacentHTML('beforeend', `
    <div class="env-row" id="proj-env-row-${esc(k)}">
      <span class="env-key">${esc(k)}</span>
      <input class="env-val-input" id="proj-env-val-${esc(k)}" type="text" value="${esc(v)}" autocomplete="off" spellcheck="false">
      <button class="btn danger" style="font-size:11px;padding:2px 8px" onclick="removeProjectEnvRow('${esc(k)}')">删除</button>
    </div>`);
  document.getElementById('project-new-env-key').value = '';
  document.getElementById('project-new-env-val').value = '';
}

function removeProjectEnvRow(key) {
  document.getElementById(`proj-env-row-${key}`)?.remove();
  const container = document.getElementById('project-env-rows');
  if (!container.querySelector('.env-row')) {
    container.innerHTML = '<div style="color:var(--text-3);font-size:12px;padding:2px 0">暂无变量</div>';
  }
}

async function saveProjectEnvVars() {
  if (!projectContext) return;
  const rows = document.querySelectorAll('#project-env-rows .env-row');
  const vars = {};
  rows.forEach(row => {
    const key = row.id.replace('proj-env-row-', '');
    const inp = row.querySelector('.env-val-input');
    if (key && inp) vars[key] = inp.value;
  });
  try {
    const r = await fetch(`${API}/api/projects/${encodeURIComponent(projectContext.name)}/env`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vars }),
    });
    const data = await r.json();
    if (!r.ok) { showProjectEnvMsg(data.detail || '保存失败', 'error'); return; }
    showProjectEnvMsg('已保存', 'ok');
  } catch (e) {
    showProjectEnvMsg(e.message, 'error');
  }
}

function showProjectEnvMsg(text, type) {
  const el = document.getElementById('project-env-msg');
  if (!el) return;
  el.textContent = text;
  el.className = type === 'error' ? 'inline-alert' : '';
  el.style.display = '';
  if (type !== 'error') setTimeout(() => { el.style.display = 'none'; }, 2000);
}

