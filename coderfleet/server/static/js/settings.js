// ══════════════════════════════════════════════════════════════
//  系统设置页（config.conf 读写 + 账号登录）
//  表单结构由后端 settings_schema 登记表驱动，这里只做渲染与提交。
// ══════════════════════════════════════════════════════════════

let settingsGroups = [];
const SETTINGS_TAB_KEY = 'coderfleet.settings.activeTab';
const SETTINGS_TABS = ['preferences', 'runtime', 'account'];

function loadSettings() {
  initSettingsPage();
  refreshCurrentSettingsTab();
}

function initSettingsPage() {
  const page = document.getElementById('page-settings');
  if (!page) return;

  const savedTab = localStorage.getItem('coderfleet.settings.activeTab') || 'preferences';
  switchSettingsTab(savedTab, { persist: false, refresh: false });
}

function switchSettingsTab(tab, options = {}) {
  const nextTab = SETTINGS_TABS.includes(tab) ? tab : 'preferences';
  const persist = options.persist !== false;
  const refresh = options.refresh !== false;

  document.querySelectorAll('.settings-tab').forEach(btn => {
    const isActive = btn.dataset.settingsTab === nextTab;
    btn.classList.toggle('active', isActive);
    btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
  });

  document.querySelectorAll('.settings-panel').forEach(panel => {
    const isActive = panel.id === `settings-panel-${nextTab}`;
    panel.classList.toggle('active', isActive);
    panel.hidden = !isActive;
  });

  if (persist) localStorage.setItem('coderfleet.settings.activeTab', nextTab);
  if (refresh) refreshCurrentSettingsTab();
}

function getCurrentSettingsTab() {
  return document.querySelector('.settings-tab.active')?.dataset.settingsTab || 'preferences';
}

function refreshCurrentSettingsTab() {
  const tab = getCurrentSettingsTab();
  if (tab === 'runtime') {
    loadRuntimeSettings();
  } else if (tab === 'account') {
    renderAccountSettings();
  } else {
    renderPreferencesSettings();
  }
}

function renderPreferencesSettings() {
  const panel = document.getElementById('settings-panel-preferences');
  if (!panel) return;
  panel.innerHTML = renderSystemPreferencesSection();
  updateThemeIcon();
  if (typeof updateNotifBtn === 'function') updateNotifBtn();
}

function loadRuntimeSettings() {
  const wrap = document.getElementById('settings-runtime-wrap');
  if (!wrap) return;
  wrap.innerHTML = '<div class="empty">加载中...</div>';
  fetch('/api/config')
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(data => {
      settingsGroups = data.groups || [];
      wrap.innerHTML = settingsGroups.map(renderSettingsGroup).join('');
      refreshTelegramStatus();
    })
    .catch(e => { wrap.innerHTML = `<div class="empty">加载失败：${esc(String(e.message || e))}</div>`; });
  if (typeof loadSharedImageBuildHistory === 'function') loadSharedImageBuildHistory();
}

function renderAccountSettings() {
  const wrap = document.getElementById('settings-account-wrap');
  if (!wrap) return;
  wrap.innerHTML = renderAccountSection();
}

function renderSystemPreferencesSection() {
  return `
  <div class="settings-card" id="settings-system-preferences">
    <div class="settings-card-head">
      <div class="settings-card-title">系统偏好</div>
      <div class="settings-card-help">通知提醒与显示主题。</div>
    </div>
    <div class="settings-preference-list">
      <button class="settings-preference-row" type="button" onclick="togglePushNotification()">
        <span class="settings-preference-main">
          <span class="settings-preference-title">系统提醒</span>
          <span class="settings-preference-desc">任务完成后发送推送通知</span>
        </span>
        <span class="settings-preference-action">
          <span class="pwa-notif-btn" id="pwa-notif-btn" aria-label="开启推送通知" title="开启任务完成推送通知">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
          </span>
        </span>
      </button>
      <button class="settings-preference-row" type="button" onclick="toggleTheme()">
        <span class="settings-preference-main">
          <span class="settings-preference-title">主题切换</span>
          <span class="settings-preference-desc">在深色与浅色模式之间切换</span>
        </span>
        <span class="settings-preference-action">
          <span class="theme-toggle-btn" id="theme-toggle-btn" aria-label="切换主题"></span>
        </span>
      </button>
    </div>
  </div>`;
}

function renderSettingsGroup(g) {
  const needApply = g.fields.some(f => f.requires_apply);
  const layoutClass = g.fields.length >= 5 ? ' settings-card-wide' : '';
  return `
  <div class="settings-card${layoutClass}" data-settings-group="${esc(g.id)}">
    <div class="settings-card-head">
      <div class="settings-card-title">${esc(g.title)}${needApply ? ' <span class="apply-badge">需 apply</span>' : ''}</div>
      ${g.help ? `<div class="settings-card-help">${esc(g.help)}</div>` : ''}
    </div>
    <div class="settings-fields">
      ${g.fields.map(renderSettingsField).join('')}
    </div>
    <div class="settings-card-foot">
      <button class="btn primary" onclick="saveSettingsGroup('${esc(g.id)}')">保存</button>
      ${g.id === 'telegram' ? '<button class="btn" onclick="testTelegram()">发送测试消息</button>' : ''}
      <span class="settings-msg" id="settings-msg-${esc(g.id)}"></span>
    </div>
  </div>`;
}

// Telegram 连通性：与 CLI `coderfleet telegram test` 能力对等
function testTelegram() {
  const msg = document.getElementById('settings-msg-telegram');
  if (!msg) return;
  msg.textContent = '发送中...';
  msg.className = 'settings-msg';
  fetch('/api/telegram/test', { method: 'POST' })
    .then(async r => {
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || ('HTTP ' + r.status));
      }
      msg.textContent = '✓ 已发送，请在 Telegram 查收';
      msg.className = 'settings-msg ok';
    })
    .catch(e => { msg.textContent = '✗ ' + String(e.message || e); msg.className = 'settings-msg err'; });
}

function refreshTelegramStatus() {
  const msg = document.getElementById('settings-msg-telegram');
  if (!msg || msg.textContent) return;
  fetch('/api/telegram/status')
    .then(r => { if (!r.ok) throw new Error(); return r.json(); })
    .then(s => {
      msg.textContent = s.configured ? `已配置 · 播报模式 ${s.notify_mode}` : '未配置';
    })
    .catch(() => {});
}

function renderSettingsField(f) {
  const id = 'set-' + f.key;
  let input;
  if (f.options && f.options.length) {
    input = `<select class="form-input" id="${id}">${
      f.options.map(o => `<option value="${esc(o)}"${o === f.value ? ' selected' : ''}>${esc(o)}</option>`).join('')
    }</select>`;
  } else if (f.secret) {
    input = `<input class="form-input" id="${id}" type="password" autocomplete="new-password" `
          + `placeholder="${f.is_set ? '已配置，留空则不修改' : esc(f.placeholder || '')}">`;
  } else {
    input = `<input class="form-input" id="${id}" type="text" value="${esc(f.value || '')}" placeholder="${esc(f.placeholder || '')}">`;
  }
  const badge = f.requires_apply ? ' <span class="apply-badge">需 apply</span>' : '';
  return `
    <div class="form-group">
      <label>${esc(f.label)}${badge}</label>
      ${input}
      ${f.help ? `<div class="field-help">${esc(f.help)}</div>` : ''}
    </div>`;
}

function renderAccountSection() {
  return `
  <div class="settings-card" id="settings-account-card">
    <div class="settings-card-head">
      <div class="settings-card-title">账号与登录</div>
      <div class="settings-card-help">退出后需重新输入访问密钥登录。</div>
    </div>
    <div class="settings-card-foot">
      <button class="btn settings-logout-btn" onclick="logoutApiKey()">退出登录</button>
    </div>
  </div>`;
}

function saveSettingsGroup(groupId) {
  const g = settingsGroups.find(x => x.id === groupId);
  if (!g) return;
  const updates = {};
  g.fields.forEach(f => {
    const el = document.getElementById('set-' + f.key);
    if (!el) return;
    const v = (el.value || '').trim();
    if (f.secret && !v) return;      // 密钥留空 = 保持不变，不提交
    updates[f.key] = v;
  });

  const msg = document.getElementById('settings-msg-' + groupId);
  msg.textContent = '保存中...';
  msg.className = 'settings-msg';

  fetch('/api/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ updates }),
  })
    .then(async r => {
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || ('保存失败 (' + r.status + ')'));
      return d;
    })
    .then(d => {
      let text = `已保存 ${(d.saved || []).length} 项`;
      if (d.requires_apply) text += '，需执行 coderfleet apply 并重启容器生效';
      msg.textContent = text;
      msg.className = 'settings-msg ok';
      // 改了 system LLM 就刷新气泡翻译入口的可用状态
      if (groupId === 'system_llm' && typeof initSystemLlmStatus === 'function') initSystemLlmStatus();
    })
    .catch(e => { msg.textContent = String(e.message || e); msg.className = 'settings-msg err'; });
}
