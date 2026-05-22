// ── 账号列表 ──────────────────────────────────────────────
async function loadAccounts() {
  try {
    const accounts = await fetch(`${API}/api/accounts`).then(r => r.json());
    globalAccountsCache = accounts;
    renderAccounts(accounts);
    populateAccountFilters(accounts);
  } catch (e) {
    document.getElementById('account-grid').innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

function renderAccounts(accounts) {
  const grid = document.getElementById('account-grid');
  if (!accounts.length) { grid.innerHTML = `<div class="empty">暂无账号配置</div>`; return; }
  grid.innerHTML = accounts.map(a => {
    const statusBadge = !a.running
      ? `<span class="badge offline">离线</span>`
      : a.busy ? `<span class="badge busy">工作中</span>` : `<span class="badge idle">空闲</span>`;
    const proxy = a.proxy || 'relay';
    const proxyBadge = `<span class="badge proxy-${proxy}">proxy: ${esc(proxy)}</span>`;
    const projectNames = a.projects || [];
    const containers = String(a.container || '').split(/\s+/).filter(Boolean);
    const projectChips = projectNames.length
      ? projectNames.map(p => `<span class="chip" title="${esc(p)}">${esc(p)}</span>`).join('')
      : `<span class="chip">未关联项目</span>`;
    const containerRows = containers.length
      ? containers.map(c => `<div class="container-name" title="${esc(c)}">${esc(c)}</div>`).join('')
      : `<div class="container-name">暂无容器</div>`;
    const runningTaskBlock = (a.busy && a.running_task_id)
      ? `<div class="account-running-task" onclick="openLogModal('${esc(a.running_task_id)}')">
          <div class="account-running-label">▶ 正在执行</div>
          <div class="account-running-prompt">${esc(a.running_task_prompt)}</div>
        </div>`
      : '';
    return `<div class="account-card">
  <div class="account-card-head">
    <div class="account-identity">
      <div class="account-badges">
        <span class="badge ${a.type}">${a.type}</span>
        <span class="badge idle">${esc(a.auth || 'login')}</span>
        ${proxyBadge}
      </div>
      <div class="account-name">${esc(a.name)}</div>
    </div>
    <div class="account-status">${statusBadge}</div>
  </div>
  <div class="account-stats">
    <div class="account-stat">
      <div class="account-stat-label">项目</div>
      <div class="account-stat-value">${projectNames.length}</div>
    </div>
    <div class="account-stat">
      <div class="account-stat-label">已完成</div>
      <div class="account-stat-value" style="color:var(--green)">${a.task_done_count ?? 0}</div>
    </div>
    <div class="account-stat">
      <div class="account-stat-label">失败</div>
      <div class="account-stat-value" style="${(a.task_failed_count ?? 0) > 0 ? 'color:var(--red)' : ''}">${a.task_failed_count ?? 0}</div>
    </div>
  </div>
  <div class="chip-list">${projectChips}</div>
  <div class="container-list">${containerRows}</div>
  ${runningTaskBlock}
  <div class="account-footer">
    <button class="btn" style="font-size:12px" onclick="filterTasksByAccount('${esc(a.name)}')">查看任务</button>
  </div>
</div>`;
  }).join('');
}

function filterTasksByAccount(accountName) {
  const sel = document.getElementById('filter-account');
  if (sel) sel.value = accountName;
  showPage('tasks');
}

function populateAccountFilters(accounts) {
  const sel = document.getElementById('filter-account');
  const prev = sel.value;
  sel.innerHTML = '<option value="">全部账号</option>' +
    accounts.map(a => `<option value="${esc(a.name)}">${esc(a.name)} (${a.type})</option>`).join('');
  sel.value = prev;
}

