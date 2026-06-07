// ── 页面切换 ──────────────────────────────────────────────
function showPage(name) {
  currentPage = name;
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.querySelector(`[data-page="${name}"]`).classList.add('active');

  const titles = { chat: '任务开发', tasks: '任务监控', boards: '开发看板', workflows: '工作流', projects: '项目管理', accounts: '账号状态', schedules: '定时计划' };
  document.getElementById('page-title').textContent = titles[name] || name;

  // 顶部 Tab 只在任务开发页展示，用于多任务链切换。
  const tabsEl = document.getElementById('topbar-tabs');
  const terminalBtn = document.getElementById('top-terminal-btn');
  const topbarTabScrollBtns = document.querySelectorAll('.topbar .tab-scroll-btn');
  if (tabsEl) tabsEl.style.display = name === 'chat' ? '' : 'none';
  topbarTabScrollBtns.forEach(btn => { btn.style.display = name === 'chat' ? '' : 'none'; });
  if (terminalBtn) terminalBtn.style.display = name === 'chat' ? '' : 'none';
  if (name !== 'chat') closeBottomTerminal(false);

  if (name === 'chat') {
    stopChatFollow();
    activeTabId = 'chat';
    activeConversationId = null;
    currentChatTaskId = null;
    renderTopbarTabs();
  }

  if (name === 'workflows') loadWorkflows();
  else if (name === 'boards') loadBoards();
  else if (name === 'schedules') initSchedulesPage();
  else refreshCurrent();
}

function refreshCurrent() {
  if (currentPage === 'chat') loadConversations();
  else if (currentPage === 'tasks') loadTasks();
  else if (currentPage === 'boards') loadBoards();
  else if (currentPage === 'workflows') loadWorkflows();
  else if (currentPage === 'projects') loadProjectsDashboard();
  else if (currentPage === 'accounts') loadAccounts();
  else if (currentPage === 'schedules') initSchedulesPage();
}

// ── 主题切换 ──────────────────────────────────────────────
function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('cf-theme', next);
  updateThemeIcon();
}

function updateThemeIcon() {
  const btn = document.getElementById('theme-toggle-btn');
  if (!btn) return;
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  btn.title = isDark ? '切换到浅色模式' : '切换到深色模式';
  btn.setAttribute('aria-label', btn.title);
  btn.innerHTML = isDark
    ? `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>`
    : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`;
}

// 监听系统主题变化（用户未手动设置时跟随系统）
if (window.matchMedia) {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
    if (!localStorage.getItem('cf-theme')) {
      document.documentElement.setAttribute('data-theme', e.matches ? 'dark' : 'light');
      updateThemeIcon();
    }
  });
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
