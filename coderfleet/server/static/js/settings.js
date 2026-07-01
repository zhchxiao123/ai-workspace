// ══════════════════════════════════════════════════════════════
//  系统设置页（config.conf 读写 + 账号登录）
//  表单结构由后端 settings_schema 登记表驱动，这里只做渲染与提交。
// ══════════════════════════════════════════════════════════════

let settingsGroups = [];

function loadSettings() {
  const wrap = document.getElementById('settings-wrap');
  if (!wrap) return;
  wrap.innerHTML = '<div class="empty">加载中...</div>';
  fetch('/api/config')
    .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(data => {
      settingsGroups = data.groups || [];
      wrap.innerHTML = settingsGroups.map(renderSettingsGroup).join('') + renderAccountSection();
    })
    .catch(e => { wrap.innerHTML = `<div class="empty">加载失败：${esc(String(e.message || e))}</div>`; });
}

function renderSettingsGroup(g) {
  const needApply = g.fields.some(f => f.requires_apply);
  return `
  <div class="settings-card">
    <div class="settings-card-head">
      <div class="settings-card-title">${esc(g.title)}${needApply ? ' <span class="apply-badge">需 apply</span>' : ''}</div>
      ${g.help ? `<div class="settings-card-help">${esc(g.help)}</div>` : ''}
    </div>
    <div class="settings-fields">
      ${g.fields.map(renderSettingsField).join('')}
    </div>
    <div class="settings-card-foot">
      <button class="btn primary" onclick="saveSettingsGroup('${esc(g.id)}')">保存</button>
      <span class="settings-msg" id="settings-msg-${esc(g.id)}"></span>
    </div>
  </div>`;
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
  <div class="settings-card">
    <div class="settings-card-head">
      <div class="settings-card-title">账号</div>
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
