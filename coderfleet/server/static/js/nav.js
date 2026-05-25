// ── 页面切换 ──────────────────────────────────────────────
function showPage(name) {
  if (name !== 'projects') disconnectProjectTerminal();
  currentPage = name;
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.querySelector(`[data-page="${name}"]`).classList.add('active');

  const titles = { chat: '任务开发', tasks: '任务监控', workflows: '工作流', projects: '项目管理', accounts: '账号状态' };
  document.getElementById('page-title').textContent = titles[name] || name;

  // 控制 Tab 系统和 + 按钮的显示/隐藏
  const tabsEl = document.getElementById('topbar-tabs');
  const addBtn = document.getElementById('add-tab-btn');
  if (tabsEl) tabsEl.style.display = (name === 'chat' || name === 'terminal') ? '' : 'none';
  if (addBtn) addBtn.style.display = (name === 'chat' || name === 'terminal') ? '' : 'none';

  if (name === 'chat') {
    activeTabId = 'chat';
    renderTopbarTabs();
  }

  if (name === 'workflows') loadWorkflows();
  else refreshCurrent();
}

function refreshCurrent() {
  if (currentPage === 'chat') loadConversations();
  else if (currentPage === 'tasks') loadTasks();
  else if (currentPage === 'workflows') loadWorkflows();
  else if (currentPage === 'projects') loadProjectsDashboard();
  else if (currentPage === 'accounts') loadAccounts();
}

// ── 健康检查 ──────────────────────────────────────────────
async function checkHealth() {
  const dot = document.getElementById('health-dot');
  const txt = document.getElementById('health-text');
  if (!dot || !txt) return;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 4000);
  try {
    const r = await fetch(`${API}/api/health`, { signal: ctrl.signal });
    clearTimeout(timer);
    dot.className = r.ok ? 'health-dot ok' : 'health-dot err';
    txt.textContent = r.ok ? '服务正常' : '服务异常';
  } catch (e) {
    clearTimeout(timer);
    dot.className = 'health-dot err';
    txt.textContent = e?.name === 'AbortError' ? '响应超时' : '无法连接';
  }
}
