// ── 账号类型注册表（动态加载） ────────────────────────────
async function loadAccountTypes() {
  try {
    const types = await fetch(`${API}/api/account-types`).then(r => r.json());
    accountTypesCache = types;
    _injectTypeBadgeStyles(types);
    _populateTypeSelects(types);
  } catch { /* 加载失败时沿用已有缓存或静态样式 */ }
}

function _injectTypeBadgeStyles(types) {
  let el = document.getElementById('type-badge-styles');
  if (!el) {
    el = document.createElement('style');
    el.id = 'type-badge-styles';
    document.head.appendChild(el);
  }
  el.textContent = types.map(t =>
    `.badge.${CSS.escape(t.id)}{background:${t.badge_bg};color:${t.badge_color}}`
  ).join('\n');
}

function _populateTypeSelects(types) {
  // 填充"新建账号"弹窗的类型下拉
  const createSel = document.getElementById('create-account-type');
  if (createSel) {
    const prev = createSel.value;
    createSel.innerHTML = types.map(t =>
      `<option value="${esc(t.id)}">${esc(t.label || t.id)}</option>`
    ).join('');
    if (types.some(t => t.id === prev)) createSel.value = prev;
  }
}

// ── 账号列表 ──────────────────────────────────────────────
async function loadAccounts() {
  try {
    const [accounts] = await Promise.all([
      fetch(`${API}/api/accounts`).then(r => r.json()),
    ]);
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
      ? `<div class="account-running-task" onclick="openLogModal('${esc(a.running_task_id)}');event.stopPropagation()">
          <div class="account-running-label">▶ 正在执行</div>
          <div class="account-running-prompt">${esc(a.running_task_prompt)}</div>
        </div>`
      : '';
    const usageBlock = renderUsageBlock(a);
    return `<div class="account-card" onclick="openAccount('${esc(a.name)}')" style="cursor:pointer">
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
  ${usageBlock}
  ${runningTaskBlock}
  <div class="account-footer">
    <button class="btn" style="font-size:12px" onclick="filterTasksByAccount('${esc(a.name)}');event.stopPropagation()">查看任务</button>
    <button class="btn" style="font-size:12px" onclick="openAccount('${esc(a.name)}');event.stopPropagation()">管理账号</button>
    <button class="btn" style="font-size:12px" onclick="openCloneAccountModal('${esc(a.name)}');event.stopPropagation()">复制</button>
  </div>
</div>`;
  }).join('');
}

const USAGE_ERROR_HINTS = {
  no_credentials: '未登录',
  unauthorized:   'token 已过期',
  rate_limited:   '被限流',
};

function _usageFillClass(pct) {
  if (pct >= 90) return 'usage-fill usage-danger';
  if (pct >= 75) return 'usage-fill usage-warn';
  return 'usage-fill';
}

function _usageRow(label, window) {
  if (!window || window.utilization == null) return '';
  const pct = Math.max(0, Math.min(100, Math.round(window.utilization)));
  const resetTitle = window.resets_at
    ? `重置：${esc(new Date(window.resets_at).toLocaleString())}`
    : '';
  return `<div class="usage-row" title="${resetTitle}">
    <span class="usage-label">${esc(label)}</span>
    <div class="usage-track"><div class="${_usageFillClass(pct)}" style="width:${pct}%"></div></div>
    <span class="usage-pct">${pct}%</span>
  </div>`;
}

function renderUsageBlock(a) {
  const usage = a.usage;
  if (!usage) return '';
  if (usage.error) {
    const hint = USAGE_ERROR_HINTS[usage.error] || usage.error;
    return `<div class="account-usage-error">
      用量：${esc(hint)}
      <a href="javascript:void(0)" onclick="refreshAccountUsage('${esc(a.name)}',event)">重试</a>
    </div>`;
  }
  const rows = [
    _usageRow('5h', usage.five_hour),
    _usageRow('7d', usage.seven_day),
  ].filter(Boolean).join('');
  if (!rows) return '';
  return `<div class="account-usage" onclick="event.stopPropagation()">
    ${rows}
    <a href="javascript:void(0)" style="font-size:11px;color:var(--text-3)" onclick="refreshAccountUsage('${esc(a.name)}',event)">刷新</a>
  </div>`;
}

async function refreshAccountUsage(name, event) {
  event?.stopPropagation();
  try {
    await fetch(`${API}/api/accounts/${encodeURIComponent(name)}/usage/refresh`, { method: 'POST' });
  } catch { /* 探测失败也重新拉一次列表，把 error 字段展示出来 */ }
  await loadAccounts();
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

// ── 账号详情 ──────────────────────────────────────────────
let accountContext = null;

function openAccount(name) {
  accountContext = (globalAccountsCache || []).find(a => a.name === name) || { name };
  document.getElementById('account-list-view').style.display = 'none';
  document.getElementById('account-detail-view').style.display = '';
  document.getElementById('account-detail-title').textContent =
    `${name}${accountContext.type ? ' · ' + accountContext.type : ''}`;
  document.getElementById('page-title').textContent = `账号 · ${name}`;
  switchAcctTab('skills');
}

function backToAccounts() {
  accountContext = null;
  document.getElementById('account-detail-view').style.display = 'none';
  document.getElementById('account-list-view').style.display = '';
  document.getElementById('page-title').textContent = '账号资源';
}

function switchAcctTab(tab) {
  document.querySelectorAll('.wf-tab[id^="acct-tab-"]').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('[id^="acct-panel-"]').forEach(el => el.style.display = 'none');
  const btn = document.getElementById(`acct-tab-${tab}`);
  const panel = document.getElementById(`acct-panel-${tab}`);
  if (btn) btn.classList.add('active');
  if (panel) panel.style.display = '';
  if (tab === 'skills'   && accountContext) loadAccountSkills(accountContext.name);
  if (tab === 'settings' && accountContext) loadAccountSettings(accountContext.name);
}

// ── 技能列表 ──────────────────────────────────────────────
async function loadAccountSkills(accountName) {
  const grid = document.getElementById('skill-grid');
  grid.innerHTML = '<div class="empty">加载中...</div>';
  try {
    const skills = await fetch(`${API}/api/accounts/${encodeURIComponent(accountName)}/skills`).then(r => r.json());
    renderSkills(skills);
  } catch (e) {
    grid.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

function renderSkills(skills) {
  const grid = document.getElementById('skill-grid');
  if (!skills.length) {
    grid.innerHTML = '<div class="empty">还没有安装任何技能，点击「+ 新建技能」开始</div>';
    return;
  }
  grid.innerHTML = skills.map(s => {
    const invokeBadges = [
      s.user_invocable ? `<span class="badge idle">用户</span>` : '',
      !s.disable_model_invocation ? `<span class="badge claude">Claude</span>` : '',
    ].filter(Boolean).join('');
    return `<div class="skill-card">
  <div class="skill-head">
    <div class="skill-slug">/${esc(s.slug)}</div>
    <div class="skill-invoke-badges">${invokeBadges || '<span class="badge offline">无触发方式</span>'}</div>
  </div>
  <div class="skill-desc">${esc(s.description || '无描述')}</div>
  <div class="skill-actions">
    <button class="btn" style="font-size:12px" onclick="openSkillEditor('${esc(s.slug)}')">编辑</button>
    <button class="btn danger" style="font-size:12px" onclick="deleteSkill('${esc(s.slug)}')">删除</button>
  </div>
</div>`;
  }).join('');
}

// ── 技能编辑器 ────────────────────────────────────────────
let _editingSkillSlug = null;

async function openSkillEditor(slug) {
  _editingSkillSlug = slug || null;
  document.getElementById('skill-modal-title').textContent = slug ? `编辑技能 /${slug}` : '新建技能';
  document.getElementById('skill-msg').style.display = 'none';

  const slugInput = document.getElementById('skill-slug');
  slugInput.disabled = !!slug;

  if (slug) {
    try {
      const skill = await fetch(
        `${API}/api/accounts/${encodeURIComponent(accountContext.name)}/skills/${encodeURIComponent(slug)}`
      ).then(r => r.json());
      slugInput.value = skill.slug;
      document.getElementById('skill-description').value = skill.description || '';
      document.getElementById('skill-user-invocable').checked = skill.user_invocable;
      document.getElementById('skill-disable-model').checked = skill.disable_model_invocation;
      document.getElementById('skill-content').value = skill.content || '';
    } catch {
      return;
    }
  } else {
    slugInput.value = '';
    document.getElementById('skill-description').value = '';
    document.getElementById('skill-user-invocable').checked = true;
    document.getElementById('skill-disable-model').checked = false;
    document.getElementById('skill-content').value = '';
  }

  document.getElementById('skill-modal').style.display = '';
}

function closeSkillModal(event) {
  if (event && event.target !== event.currentTarget) return;
  document.getElementById('skill-modal').style.display = 'none';
}

async function saveSkill() {
  const slug = _editingSkillSlug || document.getElementById('skill-slug').value.trim();
  if (!slug) {
    showSkillMsg('请填写 Slug', 'error');
    return;
  }
  const body = {
    name:                     slug,
    description:              document.getElementById('skill-description').value.trim(),
    user_invocable:           document.getElementById('skill-user-invocable').checked,
    disable_model_invocation: document.getElementById('skill-disable-model').checked,
    allowed_tools:            [],
    content:                  document.getElementById('skill-content').value,
  };
  const btn = document.getElementById('skill-save-btn');
  btn.disabled = true;
  try {
    const res = await fetch(
      `${API}/api/accounts/${encodeURIComponent(accountContext.name)}/skills/${encodeURIComponent(slug)}`,
      { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showSkillMsg(err.detail || '保存失败', 'error');
      return;
    }
    closeSkillModal();
    loadAccountSkills(accountContext.name);
  } catch (e) {
    showSkillMsg(e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

async function deleteSkill(slug) {
  if (!await confirmDialog(`确认删除技能 /${slug}？此操作不可撤销。`, { danger: true })) return;
  await fetch(
    `${API}/api/accounts/${encodeURIComponent(accountContext.name)}/skills/${encodeURIComponent(slug)}`,
    { method: 'DELETE' }
  );
  loadAccountSkills(accountContext.name);
}

function showSkillMsg(text, type) {
  const el = document.getElementById('skill-msg');
  el.textContent = text;
  el.className = type === 'error' ? 'inline-alert mt-16' : 'mt-16';
  el.style.display = '';
}

// ── 技能市场 ──────────────────────────────────────────────
let _marketSearchTimer = null;
let _pendingInstallPlugin = null;
let _marketResults = [];  // search results referenced by index from onclick

function openMarketModal() {
  document.getElementById('market-modal').style.display = '';
  document.getElementById('market-search-input').value = '';
  document.getElementById('market-grid').innerHTML = '<div class="empty" style="grid-column:1/-1">加载中...</div>';
  doMarketSearch();
}

function closeMarketModal(event) {
  if (event && event.target !== event.currentTarget) return;
  document.getElementById('market-modal').style.display = 'none';
}

function debounceMarketSearch() {
  clearTimeout(_marketSearchTimer);
  _marketSearchTimer = setTimeout(doMarketSearch, 350);
}

async function doMarketSearch() {
  clearTimeout(_marketSearchTimer);
  const q = document.getElementById('market-search-input').value.trim();
  const grid = document.getElementById('market-grid');
  const subtitle = document.getElementById('market-subtitle');
  grid.innerHTML = '<div class="empty" style="grid-column:1/-1">搜索中...</div>';
  try {
    const params = new URLSearchParams({ limit: 40 });
    if (q) params.set('q', q);
    const results = await fetch(`${API}/api/marketplace/search?${params}`).then(r => r.json());
    _marketResults = results;
    renderMarketResults(results, q);
    subtitle.textContent = q
      ? `找到 ${results.length} 个结果`
      : `来自 Anthropic 官方和社区的开源 Agent Skills（共 ${results.length} 条）`;
  } catch (e) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1">加载失败：${esc(e.message)}</div>`;
  }
}

function renderMarketResults(results, q) {
  const grid = document.getElementById('market-grid');
  if (!results.length) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1">未找到匹配的技能</div>`;
    return;
  }
  grid.innerHTML = results.map((p, idx) => {
    const verifiedBadge = p.verified
      ? `<span class="badge idle" title="官方 Anthropic 技能">官方</span>`
      : `<span class="badge proxy-relay">社区</span>`;
    const categoryBadge = p.category
      ? `<span class="badge offline">${esc(p.category)}</span>`
      : '';
    const authorText = p.author ? `<span style="color:var(--text-3);font-size:11px">${esc(p.author)}</span>` : '';
    const desc = p.description
      ? (p.description.length > 120 ? p.description.slice(0, 120) + '…' : p.description)
      : '暂无描述';
    return `<div class="market-card">
  <div class="market-card-head">
    <div class="skill-slug">/${esc(p.slug)}</div>
    <div style="display:flex;gap:4px;flex-shrink:0">${verifiedBadge}${categoryBadge}</div>
  </div>
  ${authorText}
  <div class="skill-desc" style="margin-top:6px">${esc(desc)}</div>
  <div class="skill-actions" style="margin-top:auto;padding-top:8px">
    <button class="btn primary" style="font-size:12px" onclick="openMarketInstall(${idx})">安装</button>
    ${p.homepage ? `<a class="btn" style="font-size:12px;text-decoration:none" href="${esc(p.homepage)}" target="_blank" rel="noopener">查看</a>` : ''}
  </div>
</div>`;
  }).join('');
}

function openMarketInstall(idx) {
  _pendingInstallPlugin = _marketResults[idx];
  if (!_pendingInstallPlugin) return;
  const slug = _pendingInstallPlugin.slug || '';
  document.getElementById('market-install-slug').value = slug;
  document.getElementById('market-install-title').textContent = `安装 /${esc(slug)}`;
  document.getElementById('market-install-desc').textContent = _pendingInstallPlugin.description || '';
  document.getElementById('market-install-msg').style.display = 'none';
  document.getElementById('market-install-modal').style.display = '';
}

function closeMarketInstallModal(event) {
  if (event && event.target !== event.currentTarget) return;
  document.getElementById('market-install-modal').style.display = 'none';
}

async function confirmInstall() {
  if (!_pendingInstallPlugin || !accountContext) return;
  const slug = document.getElementById('market-install-slug').value.trim();
  if (!slug) { showMarketInstallMsg('请填写 Slug', 'error'); return; }

  const btn = document.getElementById('market-install-btn');
  btn.disabled = true;
  btn.textContent = '安装中...';
  try {
    const res = await fetch(
      `${API}/api/accounts/${encodeURIComponent(accountContext.name)}/skills/install`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plugin: _pendingInstallPlugin, slug }),
      }
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showMarketInstallMsg(err.detail || '安装失败', 'error');
      return;
    }
    closeMarketInstallModal();
    closeMarketModal();
    loadAccountSkills(accountContext.name);
  } catch (e) {
    showMarketInstallMsg(e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '确认安装';
  }
}

function showMarketInstallMsg(text, type) {
  const el = document.getElementById('market-install-msg');
  el.textContent = text;
  el.className = type === 'error' ? 'inline-alert mt-16' : 'mt-16';
  el.style.display = '';
}

// ── 复制账号 ──────────────────────────────────────────────
function openCloneAccountModal(sourceName) {
  const source = (globalAccountsCache || []).find(a => a.name === sourceName);
  if (!source) return;
  const newName = prompt(`复制账号「${sourceName}」\n请输入新账号名：`, `${sourceName}_copy`);
  if (!newName || !newName.trim()) return;
  cloneAccount(source, newName.trim());
}

async function cloneAccount(source, newName) {
  try {
    const [createResp, envResp] = await Promise.all([
      fetch(`${API}/api/accounts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName, type: source.type, auth: source.auth, proxy: source.proxy }),
      }),
      fetch(`${API}/api/accounts/${encodeURIComponent(source.name)}/env`).then(r => r.json()).catch(() => ({ vars: {} })),
    ]);
    const data = await createResp.json();
    if (!createResp.ok) { alert(data.detail || '复制失败'); return; }

    const vars = envResp.vars || {};
    if (Object.keys(vars).length) {
      await fetch(`${API}/api/accounts/${encodeURIComponent(newName)}/env`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ vars }),
      });
    }

    await loadAccounts();
  } catch (e) {
    alert(e.message);
  }
}

// ── 新建账号 ──────────────────────────────────────────────
function openCreateAccountModal() {
  document.getElementById('create-account-name').value = '';
  document.getElementById('create-account-type').value = 'claude';
  document.getElementById('create-account-auth').value = 'login';
  document.getElementById('create-account-proxy').value = 'relay';
  document.getElementById('create-account-msg').style.display = 'none';
  document.getElementById('create-account-modal').style.display = '';
}

function closeCreateAccountModal(event) {
  if (event && event.target !== event.currentTarget) return;
  document.getElementById('create-account-modal').style.display = 'none';
}

async function saveNewAccount() {
  const name  = document.getElementById('create-account-name').value.trim();
  const type  = document.getElementById('create-account-type').value;
  const auth  = document.getElementById('create-account-auth').value;
  const proxy = document.getElementById('create-account-proxy').value;
  if (!name) { showCreateAccountMsg('请填写账号名', 'error'); return; }
  const btn = document.getElementById('create-account-btn');
  btn.disabled = true;
  try {
    const r = await fetch(`${API}/api/accounts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, type, auth, proxy }),
    });
    const data = await r.json();
    if (!r.ok) { showCreateAccountMsg(data.detail || '创建失败', 'error'); return; }
    closeCreateAccountModal();
    await loadAccounts();
  } catch (e) {
    showCreateAccountMsg(e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

function showCreateAccountMsg(text, type) {
  const el = document.getElementById('create-account-msg');
  el.textContent = text;
  el.className = type === 'error' ? 'inline-alert' : '';
  el.style.display = '';
}

// ── 账号设置 tab ──────────────────────────────────────────
async function loadAccountSettings(name) {
  const panel = document.getElementById('acct-panel-settings');
  panel.innerHTML = '<div class="empty">加载中...</div>';

  try {
    const [accounts, envResp] = await Promise.all([
      fetch(`${API}/api/accounts`).then(r => r.json()),
      fetch(`${API}/api/accounts/${encodeURIComponent(name)}/env`).then(r => r.json()).catch(() => ({ vars: {} })),
    ]);
    const acc = accounts.find(a => a.name === name);
    if (!acc) { panel.innerHTML = '<div class="empty">账号不存在</div>'; return; }

    const isEnv = acc.auth === 'env';
    const envVars = envResp.vars || {};
    const envRows = Object.entries(envVars).map(([k, v]) => `
      <div class="env-row" id="env-row-${esc(k)}">
        <span class="env-key">${esc(k)}</span>
        <input class="env-val-input" id="env-val-${esc(k)}" type="text" value="${esc(v)}" autocomplete="off" spellcheck="false">
        <button class="btn danger" style="font-size:11px;padding:2px 8px" onclick="removeEnvRow('${esc(k)}')">删除</button>
      </div>`).join('');

    panel.innerHTML = `
      <div style="max-width:560px;display:flex;flex-direction:column;gap:18px">
        <div class="card" style="padding:20px;display:flex;flex-direction:column;gap:14px">
          <div style="font-weight:600;font-size:13px;color:var(--text-2);margin-bottom:2px">账号配置</div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div class="form-group">
              <label class="form-label">认证方式</label>
              <select id="settings-auth" class="form-input" onchange="toggleEnvSection()">
                <option value="login" ${acc.auth==='login'?'selected':''}>login（浏览器授权）</option>
                <option value="env"   ${acc.auth==='env'  ?'selected':''}>env（API Key）</option>
              </select>
            </div>
            <div class="form-group">
              <label class="form-label">代理</label>
              <select id="settings-proxy" class="form-input">
                <option value="relay" ${acc.proxy==='relay'?'selected':''}>relay（gost 中继）</option>
                <option value="off"   ${acc.proxy==='off'  ?'selected':''}>off（直连）</option>
              </select>
            </div>
          </div>

          <div class="form-group">
            <label class="form-label">类型</label>
            <select id="settings-type" class="form-input">
              ${(accountTypesCache.length ? accountTypesCache : [{id: acc.type, label: acc.type}])
                .map(t => `<option value="${esc(t.id)}" ${acc.type===t.id?'selected':''}>${esc(t.label||t.id)}</option>`)
                .join('')}
            </select>
          </div>

          <div style="display:flex;gap:8px;align-items:center">
            <button class="btn primary" style="font-size:12px" onclick="saveAccountSettings('${esc(name)}')">保存配置</button>
            <div id="settings-msg" style="display:none"></div>
          </div>

          <!-- env 编辑区：认证方式=env 时内联展开 -->
          <div id="env-section" style="display:${isEnv?'flex':'none'};flex-direction:column;gap:10px;
               padding-top:14px;margin-top:2px;border-top:1px solid var(--border)">
            <div style="display:flex;align-items:center;gap:6px">
              <span style="font-weight:600;font-size:12px;color:var(--text-2)">环境变量</span>
              <span style="font-size:11px;color:var(--text-3)">写入账号容器 env 文件，重启容器后生效</span>
            </div>
            <div id="env-rows">${envRows || '<div style="color:var(--text-3);font-size:12px;padding:4px 0">暂无变量，在下方添加</div>'}</div>
            <div style="display:flex;gap:8px;align-items:center">
              <input id="new-env-key" class="form-input" placeholder="变量名（如 ANTHROPIC_API_KEY）" style="flex:1.4" autocomplete="off">
              <input id="new-env-val" class="form-input" type="text" placeholder="值" style="flex:1" autocomplete="off" spellcheck="false">
              <button class="btn" style="font-size:12px;white-space:nowrap" onclick="addEnvRow()">+ 添加</button>
            </div>
            <div style="display:flex;gap:8px;align-items:center">
              <button class="btn primary" style="font-size:12px" onclick="saveEnvVars('${esc(name)}')">保存环境变量</button>
              <div id="env-msg" style="display:none"></div>
            </div>
          </div>
        </div>

        <div class="card" style="padding:18px;border-color:var(--red);display:flex;flex-direction:column;gap:10px">
          <div style="font-weight:600;font-size:13px;color:var(--red)">危险操作</div>
          <div style="font-size:12px;color:var(--text-3)">从 accounts.conf 删除此账号。容器不会自动销毁，需执行「应用配置」。</div>
          <button class="btn danger" style="font-size:12px;width:fit-content" onclick="deleteAccount('${esc(name)}')">删除账号</button>
        </div>
      </div>`;
  } catch (e) {
    panel.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

function toggleEnvSection() {
  const auth = document.getElementById('settings-auth')?.value;
  const sec  = document.getElementById('env-section');
  if (sec) sec.style.display = auth === 'env' ? 'flex' : 'none';
}


function addEnvRow() {
  const k = document.getElementById('new-env-key').value.trim();
  const v = document.getElementById('new-env-val').value;
  if (!k) return;
  const container = document.getElementById('env-rows');
  // Remove "暂无变量" text if present
  const empty = container.querySelector('div[style*="color"]');
  if (empty) empty.remove();
  // Remove existing row with same key
  document.getElementById(`env-row-${k}`)?.remove();
  container.insertAdjacentHTML('beforeend', `
    <div class="env-row" id="env-row-${esc(k)}">
      <span class="env-key">${esc(k)}</span>
      <input class="env-val-input" id="env-val-${esc(k)}" type="text" value="${esc(v)}" autocomplete="off" spellcheck="false">
      <button class="btn danger" style="font-size:11px;padding:2px 8px" onclick="removeEnvRow('${esc(k)}')">删除</button>
    </div>`);
  document.getElementById('new-env-key').value = '';
  document.getElementById('new-env-val').value = '';
}

function removeEnvRow(key) {
  document.getElementById(`env-row-${key}`)?.remove();
}

async function saveAccountSettings(name) {
  const type  = document.getElementById('settings-type').value;
  const auth  = document.getElementById('settings-auth').value;
  const proxy = document.getElementById('settings-proxy').value;
  const btn   = document.querySelector('#acct-panel-settings .btn.primary');
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`${API}/api/accounts/${encodeURIComponent(name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, auth, proxy }),
    });
    const data = await r.json();
    if (!r.ok) { showSettingsMsg(data.detail || '保存失败', 'error'); return; }
    showSettingsMsg('已保存', 'ok');
    await loadAccounts();
    toggleEnvSection();
  } catch (e) {
    showSettingsMsg(e.message, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function saveEnvVars(name) {
  const rows  = document.querySelectorAll('#env-rows .env-row');
  const vars  = {};
  rows.forEach(row => {
    const key = row.id.replace('env-row-', '');
    const inp = row.querySelector('.env-val-input');
    if (key && inp) vars[key] = inp.value;
  });
  try {
    const r = await fetch(`${API}/api/accounts/${encodeURIComponent(name)}/env`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vars }),
    });
    const data = await r.json();
    if (!r.ok) { showEnvMsg(data.detail || '保存失败', 'error'); return; }
    showEnvMsg('已保存', 'ok');
  } catch (e) {
    showEnvMsg(e.message, 'error');
  }
}

async function deleteAccount(name) {
  if (!await confirmDialog(`确认删除账号「${name}」？此操作只删除配置，容器不会自动销毁。`, { danger: true })) return;
  try {
    const r = await fetch(`${API}/api/accounts/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      alert(d.detail || '删除失败');
      return;
    }
    backToAccounts();
    await loadAccounts();
  } catch (e) {
    alert(e.message);
  }
}

function showSettingsMsg(text, type) {
  const el = document.getElementById('settings-msg');
  if (!el) return;
  el.textContent = text;
  el.className = type === 'error' ? 'inline-alert' : '';
  el.style.display = '';
  if (type !== 'error') setTimeout(() => { el.style.display = 'none'; }, 2000);
}

function showEnvMsg(text, type) {
  const el = document.getElementById('env-msg');
  if (!el) return;
  el.textContent = text;
  el.className = type === 'error' ? 'inline-alert' : '';
  el.style.display = '';
  if (type !== 'error') setTimeout(() => { el.style.display = 'none'; }, 2000);
}

// ── 应用配置（system/apply） ──────────────────────────────
function openApplyModal() {
  document.getElementById('apply-output').textContent = '';
  document.getElementById('apply-output').style.display = 'none';
  document.getElementById('apply-options').style.display = '';
  document.getElementById('apply-full-checkbox').checked = false;
  document.getElementById('apply-running').style.display = 'none';
  document.getElementById('apply-done-btn').style.display = 'none';
  document.getElementById('apply-modal').style.display = '';
}

function closeApplyModal(event) {
  if (event && event.target !== event.currentTarget) return;
  document.getElementById('apply-modal').style.display = 'none';
}

async function startApply() {
  const full = document.getElementById('apply-full-checkbox').checked;
  if (full && !await confirmDialog('全量重建会销毁并重建所有容器，所有正在运行的会话都会被中断，确认继续吗？', { danger: true })) {
    return;
  }
  document.getElementById('apply-options').style.display = 'none';
  document.getElementById('apply-output').style.display = '';
  document.getElementById('apply-running').style.display = '';
  _runApply(full);
}

async function _runApply(full) {
  const out = document.getElementById('apply-output');
  try {
    const r = await fetch(`${API}/api/system/apply?full=${full ? 'true' : 'false'}`, { method: 'POST' });
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      out.textContent += decoder.decode(value, { stream: true });
      out.scrollTop = out.scrollHeight;
    }
  } catch (e) {
    out.textContent += `\n✗ 请求失败：${e.message}\n`;
  } finally {
    document.getElementById('apply-running').style.display = 'none';
    document.getElementById('apply-done-btn').style.display = '';
    loadAccounts();
  }
}

