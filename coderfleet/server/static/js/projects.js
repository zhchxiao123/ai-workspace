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
    const imageBadge = project.image
      ? `<span class="badge" title="${esc(project.image)}" style="background:var(--bg-2);color:var(--text-2)">专属镜像</span>`
      : '';
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
  <div class="account-badges" style="margin-top:10px">${accountBadge}${ideBadge}${imageBadge}${activeBadge}<span class="chip">${esc(project.account)}</span></div>
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
  const title = document.getElementById('image-build-modal-title');
  if (!modal || !output) return;

  output.textContent = '';
  running.style.display = '';
  running.textContent = '执行中…';
  doneBtn.style.display = 'none';
  title.textContent = `构建镜像 · ${projectContext.name}`;
  modal.style.display = '';

  const imageTag = document.getElementById('project-image-name')?.value.trim() || '';
  try {
    const resp = await fetch(`${API}/api/projects/${encodeURIComponent(projectContext.name)}/image/build`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_tag: imageTag }),
    });
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      output.textContent = d.detail || '请求失败';
      running.textContent = '✗ 失败';
      doneBtn.style.display = '';
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
    // 刷新项目列表，更新 image badge
    await loadProjectsDashboard();
    await loadProjectImage(projectContext.name);
  } catch (e) {
    output.textContent += `\n✗ 错误：${e.message}`;
    running.textContent = '✗ 失败';
    doneBtn.style.display = '';
  }
}

async function clearProjectImage() {
  if (!projectContext) return;
  if (!confirm('确认恢复使用共享镜像？执行 apply 后生效。')) return;
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
