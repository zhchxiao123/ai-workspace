// ── 全局缓存（accounts + conversations + 账号类型），低频刷新 ──
async function refreshGlobalCaches() {
  try {
    const [accounts, convs] = await Promise.all([
      fetch(`${API}/api/accounts`).then(r => r.json()).catch(() => []),
      fetch(`${API}/api/conversations`).then(r => r.json()).catch(() => []),
    ]);
    globalAccountsCache = accounts;
    convs.forEach(c => { conversationsCache[c.id] = c.name; });
    if (currentPage === 'tasks') populateAccountFilters(accounts);
    if (currentPage === 'accounts') {
      renderAccounts(accounts);
      populateAccountFilters(accounts);
    }
  } catch { }
  // 账号类型元数据（低频，与 accounts 并行但独立，失败不影响主缓存）
  loadAccountTypes().catch(() => {});
}

// ── 任务列表 ──────────────────────────────────────────────
async function loadTasks(options = {}) {
  if (options.resetPage) taskPage = 1;
  const status = document.getElementById('filter-status').value;
  const account = document.getElementById('filter-account').value;
  let url = `${API}/api/tasks?limit=100`;
  if (status) url += `&status=${status}`;
  if (account) url += `&account=${encodeURIComponent(account)}`;
  try {
    const tasks = await fetch(url).then(r => r.json());
    updateTaskMetrics(tasks, globalAccountsCache);
    taskRowsCache = [...tasks].sort((a, b) => new Date(b.created || 0) - new Date(a.created || 0));
    renderTasks(tasks);
  } catch (e) {
    document.getElementById('task-tbody').innerHTML =
      `<tr><td colspan="7"><div class="empty">加载失败：${esc(e.message)}</div></td></tr>`;
  }
}

function renderTasks(tasks) {
  const tbody = document.getElementById('task-tbody');
  const rows = taskRowsCache.length || tasks.length ? taskRowsCache : tasks;
  const pageCount = Math.max(1, Math.ceil(rows.length / TASK_PAGE_SIZE));
  taskPage = Math.min(Math.max(taskPage, 1), pageCount);
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="7"><div class="empty">暂无任务记录</div></td></tr>`;
    renderTaskPagination(0, 0, 0);
    return;
  }
  const start = (taskPage - 1) * TASK_PAGE_SIZE;
  const pageRows = rows.slice(start, start + TASK_PAGE_SIZE);
  tbody.innerHTML = pageRows.map(t => {
    const convName = t.conversation_id ? (conversationsCache[t.conversation_id] || t.conversation_id.slice(0, 8)) : '';
    const convBadge = convName
      ? `<span class="chain-badge" title="任务链: ${esc(convName)}">${esc(convName)}</span>`
      : '';
    const dur = fmtDuration(t.created, t.finished);
    return `
  <tr onclick="openLogModal('${t.id}')">
    <td><span class="status-dot ${t.status}">${statusLabel(t.status)}</span></td>
    <td class="task-desc-cell"><div class="task-desc-wrap">${convBadge}<div class="task-prompt" title="${esc(t.prompt)}">${esc(t.prompt)}</div></div></td>
    <td><span class="badge ${t.type}">${t.type}</span> <span style="font-size:12px">${esc(t.account)}</span></td>
    <td><div class="task-project" title="${esc(t.project)}">${esc(t.project.split('/').pop())}</div></td>
    <td class="text-muted text-sm">${fmtTime(t.created)}</td>
    <td class="duration-cell${t.status === 'running' ? ' running' : ''}">${dur}</td>
    <td onclick="event.stopPropagation()">
      ${t.status === 'running' ? `<button class="btn danger" style="padding:3px 8px;font-size:12px" onclick="killTask('${t.id}')">终止</button>` : ''}
    </td>
  </tr>`;
  }).join('');
  renderTaskPagination(rows.length, start + 1, Math.min(start + TASK_PAGE_SIZE, rows.length));
}

function renderTaskPagination(total, start, end) {
  const bar = document.getElementById('task-pagination');
  if (!bar) return;
  if (!total) {
    bar.style.display = 'none';
    bar.innerHTML = '';
    return;
  }
  const pageCount = Math.max(1, Math.ceil(total / TASK_PAGE_SIZE));
  bar.style.display = '';
  bar.innerHTML = `<span>显示 ${start}-${end} / ${total} 条 · 第 ${taskPage} / ${pageCount} 页</span>
<div class="pagination-actions">
  <button class="btn" style="font-size:12px" onclick="setTaskPage(${taskPage - 1})" ${taskPage <= 1 ? 'disabled' : ''}>上一页</button>
  <button class="btn" style="font-size:12px" onclick="setTaskPage(${taskPage + 1})" ${taskPage >= pageCount ? 'disabled' : ''}>下一页</button>
</div>`;
}

function setTaskPage(page) {
  taskPage = page;
  renderTasks(taskRowsCache);
}
