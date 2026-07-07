
// ── 认证检查 ──────────────────────────────────────────────
if (!getApiKey()) showLoginOverlay();

// ── 初始化 ────────────────────────────────────────────────
updateThemeIcon();
initSidebarState();
initChatSidebarState();
initTopbarTabs();
initBottomTerminalDrawer();
try { initChatSortBtn(); } catch {}
checkHealth();
refreshGlobalCaches();
loadConversations();
loadAccountOptions();
window.addEventListener('resize', resizeBottomTerminal);
window.addEventListener('beforeunload', disconnectAllBottomTerminals);

// 高频刷新：任务状态（5s，仅当前页）
setInterval(() => {
  checkHealth();
  if (currentPage === 'chat' && !chatFollowMode) pollChatHeartbeat();
  if (currentPage === 'tasks') loadTasks();
  if (currentPage === 'accounts') loadAccounts();
  if (currentPage === 'workflows' && wfActiveTab === 'runs') loadWorkflows();
}, 5000);

// 低频刷新：全局账号 + 任务链缓存（30s，后台保持同步）
setInterval(refreshGlobalCaches, 30000);
