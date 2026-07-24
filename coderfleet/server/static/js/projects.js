// ── 项目工作台 ────────────────────────────────────────────
// 项目归属判断函数已统一迁移至 js/shared/project-utils.js
// （脚本在 utils 之后、projects 之前加载，保证全局可用）

let activeImageBuildId = '';
let activeImageBuildProject = '';
let activeImageBuildController = null;

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
    const imageBadge = project.image
      ? `<span class="badge" title="${esc(project.image)}" style="background:var(--bg-2);color:var(--text-2)">专属镜像</span>`
      : '';
  const ideBadge = project.ide_enabled
      ? `<span class="badge running">IDE ${esc(project.ide_port || '')}</span>`
      : '';
    const activeBadge = project.active === false
      ? `<span class="badge offline">容器停用</span>`
      : '';
    const containerRunning = project.container_running === true;
    const statusBadge = project.container_running == null
      ? ''
      : containerRunning
        ? `<span class="badge running">● 运行中</span>`
        : `<span class="badge offline">○ 已停止</span>`;
    const containerToggleBtn = containerRunning
      ? `<button class="btn" style="font-size:12px" onclick="event.stopPropagation();stopProjectContainer('${esc(project.name)}', this)">停止</button>`
      : `<button class="btn" style="font-size:12px" onclick="event.stopPropagation();startProjectContainer('${esc(project.name)}', this)">启动</button>`;
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
  <div class="account-badges" style="margin-top:10px">${statusBadge}${accountBadge}${ideBadge}${imageBadge}${activeBadge}<span class="chip">${esc(project.account)}</span></div>
  <div class="project-stats">
    <div class="project-stat"><div class="account-stat-label">总任务</div><div class="account-stat-value">${projectTasks.length}</div></div>
    <div class="project-stat"><div class="account-stat-label">运行中</div><div class="account-stat-value">${running}</div></div>
    <div class="project-stat"><div class="account-stat-label">完成</div><div class="account-stat-value">${projectTasks.filter(t => t.status === 'done').length}</div></div>
    <div class="project-stat"><div class="account-stat-label">失败</div><div class="account-stat-value">${failed}</div></div>
  </div>
  <div class="account-meta" style="margin-top:10px" ${latest ? `title="${esc(latest.prompt)}"` : ''}>${latestText}</div>
  <div class="account-footer" style="margin-top:10px">
    ${containerToggleBtn}
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
  await Promise.all([loadProjectsDashboard(), loadProjectEnvVars(name), loadProjectImage(name)]);
}

function backToProjects() {
  const frame = document.getElementById('project-ide-frame');
  if (frame) frame.src = 'about:blank';
  disconnectProjectTerminal?.();
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
  const ideUrl  = projectIdeUrl(project);

  document.getElementById('project-detail-summary').innerHTML = `
<div class="project-title">${esc(project.name)}</div>
<div class="project-path" title="${esc(project.path)}">${esc(project.path)}</div>
<div class="account-badges" style="margin-top:12px">
  <span class="chip">账号 ${esc(project.account)}</span>
  ${account ? `<span class="badge ${account.type}">${account.type}</span><span class="badge proxy-${account.proxy || 'relay'}">proxy: ${esc(account.proxy || 'relay')}</span>` : `<span class="badge offline">账号缺失</span>`}
  ${project.active === false ? `<span class="badge offline">已停用</span>` : ''}
  ${project.image ? `<span class="badge" style="background:var(--bg-2);color:var(--text-2)" title="${esc(project.image)}">专属镜像</span>` : ''}
</div>
<div class="project-stats" style="margin-top:16px;margin-bottom:${ideUrl ? '16px' : '0'}">
  <div class="project-stat"><div class="account-stat-label">总任务</div><div class="account-stat-value">${projectTasks.length}</div></div>
  <div class="project-stat"><div class="account-stat-label">运行中</div><div class="account-stat-value" style="${running > 0 ? 'color:var(--green)' : ''}">${running}</div></div>
  <div class="project-stat"><div class="account-stat-label">完成</div><div class="account-stat-value">${done}</div></div>
  <div class="project-stat"><div class="account-stat-label">失败</div><div class="account-stat-value" style="${failed > 0 ? 'color:var(--red)' : ''}">${failed}</div></div>
</div>
${ideUrl ? `<div style="border-top:1px solid var(--border);padding-top:14px">
  <div style="font-size:11px;color:var(--text-3);margin-bottom:8px">浏览器 IDE</div>
  <a href="${esc(ideUrl)}" target="_blank" rel="noopener" class="btn" style="font-size:12px;display:inline-flex;align-items:center;gap:5px">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:13px;height:13px"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
    打开 IDE · :${esc(String(project.ide_port || ''))}
  </a>
</div>` : ''}`;
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
let _projectAccountsCache = [];
let _selectedSecondaryAccounts = [];

async function openProjectFormModal(name) {
  _editingProjectName = name || null;
  document.getElementById('project-form-title').textContent = name ? `编辑项目 · ${name}` : '新建项目';
  document.getElementById('project-form-msg').style.display = 'none';

  const nameInput = document.getElementById('project-form-name');
  nameInput.disabled = !!name;

  // 填充账号下拉
  _projectAccountsCache = (await fetch(`${API}/api/accounts`).then(r => r.json()).catch(() => []));
  const sel = document.getElementById('project-form-account');
  sel.innerHTML = _projectAccountsCache.map(a =>
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
      document.getElementById('project-form-docker-socket').value = project.docker_socket || '';
      _selectedSecondaryAccounts = [...(project.secondary_accounts || [])];
    }
  } else {
    nameInput.value = '';
    sel.value = _projectAccountsCache[0]?.name || '';
    document.getElementById('project-form-path').value = '';
    document.getElementById('project-form-active').checked = true;
    document.getElementById('project-form-ide-enabled').checked = false;
    document.getElementById('project-form-ide-port').value = '';
    document.getElementById('project-form-ide-auth').value = 'none';
    document.getElementById('project-form-ide-remote').checked = false;
    document.getElementById('project-form-docker-socket').value = '';
    _selectedSecondaryAccounts = [];
  }
  toggleProjectIdePort();
  renderSecondaryAccountPicker();
  renderProjectSecondaryAccountsRows();

  document.getElementById('project-form-modal').style.display = '';
}

function onProjectAccountChanged() {
  const primaryAccount = document.getElementById('project-form-account').value;
  _selectedSecondaryAccounts = _selectedSecondaryAccounts.filter(n => n !== primaryAccount);
  renderSecondaryAccountPicker();
  renderProjectSecondaryAccountsRows();
}

function renderSecondaryAccountPicker() {
  const picker = document.getElementById('project-form-secondary-account-picker');
  const primaryAccount = document.getElementById('project-form-account').value;
  const primaryType = _projectAccountsCache.find(a => a.name === primaryAccount)?.type;
  const selectedTypes = new Set(
    _selectedSecondaryAccounts.map(n => _projectAccountsCache.find(a => a.name === n)?.type)
  );
  // 同一项目不能绑定两个相同类型的账号：候选列表按 TYPE 过滤，排除主账号类型及已选从账号的类型
  const candidates = _projectAccountsCache.filter(a => a.type !== primaryType && !selectedTypes.has(a.type));
  picker.innerHTML = candidates.map(a =>
    `<option value="${esc(a.name)}">${esc(a.name)} (${a.type})</option>`
  ).join('');
}

function renderProjectSecondaryAccountsRows() {
  const container = document.getElementById('project-form-secondary-accounts-rows');
  if (!_selectedSecondaryAccounts.length) {
    container.innerHTML = '<div style="color:var(--text-3);font-size:12px;padding:2px 0">未绑定从账号</div>';
    return;
  }
  container.innerHTML = _selectedSecondaryAccounts.map(name => {
    const acc = _projectAccountsCache.find(a => a.name === name);
    return `
      <div class="secondary-account-row">
        <span class="secondary-account-name" title="${esc(name)}">
          ${esc(name)}
          ${acc ? `<span class="secondary-account-type">${esc(acc.type)}</span>` : ''}
        </span>
        <button type="button" class="btn danger secondary-account-remove" title="移除从账号" onclick="removeProjectSecondaryAccountRow('${esc(name)}')">×</button>
      </div>`;
  }).join('');
}

function addProjectSecondaryAccount() {
  const picker = document.getElementById('project-form-secondary-account-picker');
  const name = picker.value;
  if (!name || _selectedSecondaryAccounts.includes(name)) return;
  _selectedSecondaryAccounts.push(name);
  renderSecondaryAccountPicker();
  renderProjectSecondaryAccountsRows();
}

function removeProjectSecondaryAccountRow(name) {
  _selectedSecondaryAccounts = _selectedSecondaryAccounts.filter(n => n !== name);
  renderSecondaryAccountPicker();
  renderProjectSecondaryAccountsRows();
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
  const dockerSocket = document.getElementById('project-form-docker-socket').value.trim();
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
        ? { account, path, active, ide_enabled: ideEnabled, ide_port: idePort, ide_auth: ideAuth, ide_remote: ideRemote, docker_socket: dockerSocket, secondary_accounts: _selectedSecondaryAccounts }
        : { name, account, path, active, ide_enabled: ideEnabled, ide_port: idePort, ide_auth: ideAuth, ide_remote: ideRemote, docker_socket: dockerSocket, secondary_accounts: _selectedSecondaryAccounts }),
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
  if (!await confirmDialog(`确认删除项目「${name}」？容器不会自动销毁，配置从 projects.conf 中移除。`, { danger: true })) return;
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

async function startProjectContainer(name, btn) {
  const originalLabel = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '启动中…'; }
  try {
    const r = await fetch(`${API}/api/projects/${encodeURIComponent(name)}/container/start`, { method: 'POST' });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      showToast(d.detail || '启动失败', 'error');
      return;
    }
    showToast('项目已启动', 'success');
    await loadProjectsDashboard();
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
  }
}

async function stopProjectContainer(name, btn) {
  const originalLabel = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '停止中…'; }
  try {
    const r = await fetch(`${API}/api/projects/${encodeURIComponent(name)}/container/stop`, { method: 'POST' });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      showToast(d.detail || '停止失败', 'error');
      return;
    }
    showToast('项目已停止', 'success');
    await loadProjectsDashboard();
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
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

// ── 项目镜像管理 ──────────────────────────────────────────

async function loadProjectImage(name) {
  const project = (projectsCache || []).find(p => p.name === name);
  const badge = document.getElementById('project-image-badge');
  const resetBtn = document.getElementById('project-image-reset-btn');
  const nameInput = document.getElementById('project-image-name');
  if (badge) {
    if (project?.image) {
      badge.textContent = project.image;
      badge.className = 'badge running';
      if (resetBtn) resetBtn.style.display = '';
    } else {
      badge.textContent = '共享镜像';
      badge.className = 'badge';
      if (resetBtn) resetBtn.style.display = 'none';
    }
  }
  if (nameInput) {
    nameInput.value = project?.image || '';
    nameInput.placeholder = `coderfleet-${name}:latest`;
  }
  try {
    const resp = await fetch(`${API}/api/projects/${encodeURIComponent(name)}/dockerfile`).then(r => r.json());
    const ta = document.getElementById('project-image-dockerfile');
    if (ta) ta.value = resp.content || '';
  } catch {
    // ignore
  }
  await loadProjectBuildHistory(name);
}

// ── 构建历史 ──────────────────────────────────────────────

const BUILD_STATUS_LABEL = {
  running: '执行中', succeeded: '成功', failed: '失败', cancelled: '已停止',
};

async function loadProjectBuildHistory(name) {
  const list = document.getElementById('project-build-history-list');
  if (!list || !name) return;
  try {
    const builds = await fetch(`${API}/api/builds?project=${encodeURIComponent(name)}&limit=20`).then(r => r.json());
    if (!Array.isArray(builds) || builds.length === 0) {
      list.innerHTML = '<div style="font-size:12px;color:var(--text-3)">暂无构建记录</div>';
      return;
    }
    list.innerHTML = builds.map(b => {
      const label = BUILD_STATUS_LABEL[b.status] || b.status;
      const badgeClass = b.status === 'succeeded' ? 'ok' : (b.status === 'failed' ? 'error' : (b.status === 'running' ? 'running' : ''));
      return `
        <div class="build-history-row" style="display:flex;align-items:center;gap:10px;padding:6px 8px;border:1px solid var(--border);border-radius:6px;cursor:pointer;font-size:12px"
             onclick="viewBuildHistoryEntry('${esc(b.id)}', '${esc(name)}')">
          <span class="badge ${badgeClass}" style="font-size:11px">${esc(label)}</span>
          <span style="font-family:var(--mono);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(b.image_tag)}</span>
          <span style="color:var(--text-3)">${esc(fmtTime(b.created))}</span>
          <span style="color:var(--text-3)">${esc(fmtDuration(b.created, b.finished))}</span>
        </div>`;
    }).join('');
  } catch {
    list.innerHTML = '<div style="font-size:12px;color:var(--text-3)">构建历史加载失败</div>';
  }
}

let activeBuildHistoryEventSource = null;

function closeBuildHistoryStream() {
  if (activeBuildHistoryEventSource) {
    activeBuildHistoryEventSource.close();
    activeBuildHistoryEventSource = null;
  }
}

async function viewBuildHistoryEntry(buildId, projectName) {
  closeBuildHistoryStream();
  const modal = document.getElementById('image-build-modal');
  const output = document.getElementById('image-build-output');
  const running = document.getElementById('image-build-running');
  const doneBtn = document.getElementById('image-build-done-btn');
  const stopBtn = document.getElementById('image-build-stop-btn');
  const title = document.getElementById('image-build-modal-title');
  if (!modal || !output) return;

  let build;
  try {
    build = await fetch(`${API}/api/builds/${encodeURIComponent(buildId)}`).then(r => r.json());
  } catch {
    return;
  }

  title.textContent = projectName ? `构建历史 · ${projectName}` : '构建历史 · 共享镜像';
  output.textContent = await fetch(`${API}/api/builds/${encodeURIComponent(buildId)}/logs`).then(r => r.ok ? r.text() : '').catch(() => '');
  output.scrollTop = output.scrollHeight;
  doneBtn.style.display = '';

  if (build.status === 'running') {
    running.style.display = '';
    running.textContent = '执行中…';
    if (stopBtn) {
      stopBtn.style.display = '';
      stopBtn.disabled = false;
      stopBtn.textContent = '停止构建';
    }
    activeImageBuildId = buildId;
    activeImageBuildProject = projectName;
    // skip_bytes 是服务端日志文件里的字节偏移量，不能直接用 JS 字符串的 .length——
    // 构建日志里带中文（"镜像构建完成" 之类），UTF-16 code unit 数和 UTF-8 字节数对不上，
    // 会导致续传时重复或漏掉一段内容。用 TextEncoder 编码成实际字节数再传。
    const skipBytes = new TextEncoder().encode(output.textContent).length;
    const es = new EventSource(`${API}/api/builds/${encodeURIComponent(buildId)}/logs/stream?skip_bytes=${skipBytes}`);
    activeBuildHistoryEventSource = es;
    es.onmessage = (event) => {
      if (event.data === '[DONE]') {
        closeBuildHistoryStream();
        running.textContent = '已完成';
        if (stopBtn) stopBtn.style.display = 'none';
        if (projectName) loadProjectBuildHistory(projectName);
        else if (typeof loadSharedImageBuildHistory === 'function') loadSharedImageBuildHistory();
        return;
      }
      output.textContent += `${event.data}\n`;
      output.scrollTop = output.scrollHeight;
    };
    es.onerror = () => {
      // 连接异常断开且没收到 [DONE]：回退成一次性查询最终状态，避免面板永远卡在"执行中…"。
      closeBuildHistoryStream();
      fetch(`${API}/api/builds/${encodeURIComponent(buildId)}`).then(r => r.json()).then(b => {
        if (b && b.status !== 'running') {
          running.textContent = BUILD_STATUS_LABEL[b.status] || b.status;
          if (stopBtn) stopBtn.style.display = 'none';
        }
      }).catch(() => {});
    };
  } else {
    running.style.display = 'none';
    if (stopBtn) stopBtn.style.display = 'none';
  }

  modal.style.display = '';
}

// ── 共享镜像构建（Settings · 运行配置）─────────────────────

async function loadSharedImageBuildHistory() {
  const list = document.getElementById('shared-image-build-history-list');
  if (!list) return;
  try {
    const builds = await fetch(`${API}/api/builds?kind=shared&limit=20`).then(r => r.json());
    if (!Array.isArray(builds) || builds.length === 0) {
      list.innerHTML = '<div style="font-size:12px;color:var(--text-3)">暂无构建记录</div>';
      return;
    }
    list.innerHTML = builds.map(b => {
      const label = BUILD_STATUS_LABEL[b.status] || b.status;
      const badgeClass = b.status === 'succeeded' ? 'ok' : (b.status === 'failed' ? 'error' : (b.status === 'running' ? 'running' : ''));
      return `
        <div class="build-history-row" style="display:flex;align-items:center;gap:10px;padding:6px 8px;border:1px solid var(--border);border-radius:6px;cursor:pointer;font-size:12px"
             onclick="viewBuildHistoryEntry('${esc(b.id)}', '')">
          <span class="badge ${badgeClass}" style="font-size:11px">${esc(label)}</span>
          <span style="font-family:var(--mono);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(b.image_tag)}</span>
          <span style="color:var(--text-3)">${esc(fmtTime(b.created))}</span>
          <span style="color:var(--text-3)">${esc(fmtDuration(b.created, b.finished))}</span>
        </div>`;
    }).join('');
  } catch {
    list.innerHTML = '<div style="font-size:12px;color:var(--text-3)">构建历史加载失败</div>';
  }
}

async function buildSharedImage() {
  const modal = document.getElementById('image-build-modal');
  const output = document.getElementById('image-build-output');
  const running = document.getElementById('image-build-running');
  const doneBtn = document.getElementById('image-build-done-btn');
  const stopBtn = document.getElementById('image-build-stop-btn');
  const title = document.getElementById('image-build-modal-title');
  if (!modal || !output) return;

  output.textContent = '';
  running.style.display = '';
  running.textContent = '执行中…';
  doneBtn.style.display = 'none';
  if (stopBtn) {
    stopBtn.style.display = '';
    stopBtn.disabled = false;
    stopBtn.textContent = '停止构建';
  }
  title.textContent = '构建共享镜像';
  modal.style.display = '';

  const buildId = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const controller = new AbortController();
  activeImageBuildId = buildId;
  activeImageBuildProject = '';
  activeImageBuildController = controller;
  try {
    const resp = await fetch(`${API}/api/image/build`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ build_id: buildId }),
      signal: controller.signal,
    });
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      output.textContent = d.detail || '请求失败';
      running.textContent = '✗ 失败';
      doneBtn.style.display = '';
      if (stopBtn) stopBtn.style.display = 'none';
      return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      output.textContent += decoder.decode(value, { stream: true });
      output.scrollTop = output.scrollHeight;
    }
    running.textContent = '已完成';
    doneBtn.style.display = '';
    if (stopBtn) stopBtn.style.display = 'none';
    await loadSharedImageBuildHistory();
  } catch (e) {
    if (e.name === 'AbortError') {
      running.textContent = '已停止';
    } else {
      output.textContent += `\n✗ 错误：${e.message}`;
      running.textContent = '✗ 失败';
    }
    doneBtn.style.display = '';
    if (stopBtn) stopBtn.style.display = 'none';
  } finally {
    if (activeImageBuildId === buildId) {
      activeImageBuildId = '';
      activeImageBuildProject = '';
      activeImageBuildController = null;
    }
  }
}

async function saveProjectDockerfile() {
  if (!projectContext) return;
  const content = document.getElementById('project-image-dockerfile')?.value || '';
  try {
    const r = await fetch(`${API}/api/projects/${encodeURIComponent(projectContext.name)}/dockerfile`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      const msg = r.status === 404 ? '接口未找到，请重启 coderfleet server 后刷新页面' : (d.detail || '保存失败');
      showProjectImageMsg(msg, 'error');
      return;
    }
    showProjectImageMsg('Dockerfile 已保存', 'ok');
  } catch (e) {
    showProjectImageMsg(e.message, 'error');
  }
}

async function buildProjectImage() {
  if (!projectContext) return;
  const content = document.getElementById('project-image-dockerfile')?.value || '';
  if (!content.trim()) {
    showProjectImageMsg('请先填写 Dockerfile 内容', 'error');
    return;
  }

  // 先保存 Dockerfile
  try {
    const r = await fetch(`${API}/api/projects/${encodeURIComponent(projectContext.name)}/dockerfile`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      const msg = r.status === 404 ? '接口未找到，请重启 coderfleet server 后刷新页面' : (d.detail || 'Dockerfile 保存失败');
      showProjectImageMsg(msg, 'error');
      return;
    }
  } catch (e) {
    showProjectImageMsg(e.message, 'error');
    return;
  }

  // 打开构建 Modal
  const modal = document.getElementById('image-build-modal');
  const output = document.getElementById('image-build-output');
  const running = document.getElementById('image-build-running');
  const doneBtn = document.getElementById('image-build-done-btn');
  const stopBtn = document.getElementById('image-build-stop-btn');
  const title = document.getElementById('image-build-modal-title');
  if (!modal || !output) return;

  output.textContent = '';
  running.style.display = '';
  running.textContent = '执行中…';
  doneBtn.style.display = 'none';
  if (stopBtn) {
    stopBtn.style.display = '';
    stopBtn.disabled = false;
    stopBtn.textContent = '停止构建';
  }
  title.textContent = `构建镜像 · ${projectContext.name}`;
  modal.style.display = '';

  const imageTag = document.getElementById('project-image-name')?.value.trim() || '';
  const buildId = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const controller = new AbortController();
  activeImageBuildId = buildId;
  activeImageBuildProject = projectContext.name;
  activeImageBuildController = controller;
  try {
    const resp = await fetch(`${API}/api/projects/${encodeURIComponent(projectContext.name)}/image/build`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_tag: imageTag, build_id: buildId }),
      signal: controller.signal,
    });
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      output.textContent = d.detail || '请求失败';
      running.textContent = '✗ 失败';
      doneBtn.style.display = '';
      if (stopBtn) stopBtn.style.display = 'none';
      return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      output.textContent += decoder.decode(value, { stream: true });
      output.scrollTop = output.scrollHeight;
    }
    running.textContent = '已完成';
    doneBtn.style.display = '';
    if (stopBtn) stopBtn.style.display = 'none';
    // 刷新项目列表，更新 image badge
    await loadProjectsDashboard();
    await loadProjectImage(projectContext.name);
  } catch (e) {
    if (e.name === 'AbortError') {
      running.textContent = '已停止';
    } else {
      output.textContent += `\n✗ 错误：${e.message}`;
      running.textContent = '✗ 失败';
    }
    doneBtn.style.display = '';
    if (stopBtn) stopBtn.style.display = 'none';
  } finally {
    if (activeImageBuildId === buildId) {
      activeImageBuildId = '';
      activeImageBuildProject = '';
      activeImageBuildController = null;
    }
  }
}

async function stopProjectImageBuild() {
  if (!activeImageBuildId) return;
  const stopBtn = document.getElementById('image-build-stop-btn');
  const running = document.getElementById('image-build-running');
  const output = document.getElementById('image-build-output');
  const doneBtn = document.getElementById('image-build-done-btn');
  if (stopBtn) {
    stopBtn.disabled = true;
    stopBtn.textContent = '停止中…';
  }
  if (running) running.textContent = '停止中…';
  const projectName = activeImageBuildProject;
  // 共享镜像构建没有 project 归属，走独立的 /api/image/build/{id} 停止接口。
  const stopUrl = projectName
    ? `${API}/api/projects/${encodeURIComponent(projectName)}/image/build/${encodeURIComponent(activeImageBuildId)}`
    : `${API}/api/image/build/${encodeURIComponent(activeImageBuildId)}`;
  try {
    const r = await fetch(stopUrl, { method: 'DELETE' });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || '停止失败');
    if (output && d.message) output.textContent += `\n${d.message}\n`;
    if (activeImageBuildController) activeImageBuildController.abort();
    if (running) running.textContent = d.ok ? '已停止' : (d.message || '已结束');
  } catch (e) {
    if (output) output.textContent += `\n✗ 停止失败：${e.message}\n`;
    if (running) running.textContent = '停止失败';
    if (stopBtn) {
      stopBtn.disabled = false;
      stopBtn.textContent = '停止构建';
    }
    return;
  }
  if (stopBtn) stopBtn.style.display = 'none';
  if (doneBtn) doneBtn.style.display = '';
}

async function clearProjectImage() {
  if (!projectContext) return;
  if (!await confirmDialog('确认恢复使用共享镜像？执行 apply 后生效。')) return;
  try {
    const r = await fetch(`${API}/api/projects/${encodeURIComponent(projectContext.name)}/image`, {
      method: 'DELETE',
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      showProjectImageMsg(d.detail || '操作失败', 'error');
      return;
    }
    showProjectImageMsg('已恢复共享镜像', 'ok');
    await loadProjectsDashboard();
    await loadProjectImage(projectContext.name);
  } catch (e) {
    showProjectImageMsg(e.message, 'error');
  }
}

function closeImageBuildModal(event) {
  if (event && event.target !== event.currentTarget) return;
  closeBuildHistoryStream();
  document.getElementById('image-build-modal').style.display = 'none';
}

function showProjectImageMsg(text, type) {
  const el = document.getElementById('project-image-msg');
  if (!el) return;
  el.textContent = text;
  el.className = type === 'error' ? 'inline-alert' : '';
  el.style.display = '';
  if (type !== 'error') setTimeout(() => { el.style.display = 'none'; }, 2500);
}
