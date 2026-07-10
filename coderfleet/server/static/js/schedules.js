// ── 定时计划管理页 ────────────────────────────────────────

const DAY_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const HOUR_OPTIONS = Array.from({length: 24}, (_, i) => String(i).padStart(2, '0'));
const MINUTE_OPTIONS = ['00', '05', '10', '15', '20', '25', '30', '35', '40', '45', '50', '55'];

let schedulesCache = [];
let editingScheduleId = null;
let scheduleTemplatesCache = [];

async function loadSchedules() {
  try {
    const r = await fetch(`${API}/api/schedules`);
    schedulesCache = await r.json();
    renderSchedules(schedulesCache);
  } catch (e) {
    document.getElementById('schedules-grid').innerHTML =
      `<div class="empty">加载失败：${e.message}</div>`;
  }
}

function renderSchedules(schedules) {
  const grid = document.getElementById('schedules-grid');
  if (!schedules.length) {
    grid.innerHTML = '<div class="empty">暂无定时计划，点击「新建计划」创建第一个</div>';
    return;
  }
  grid.innerHTML = schedules.map(s => scheduleCard(s)).join('');
}

function schedulePatternLabel(s) {
  if (s.schedule_type === 'daily') {
    return `每天 ${s.time_of_day || '--:--'}`;
  }
  if (s.schedule_type === 'weekly') {
    const days = (s.days_of_week || []).map(d => DAY_LABELS[d]).join('、');
    return `每周 ${days || '?'} ${s.time_of_day || '--:--'}`;
  }
  if (s.schedule_type === 'hourly') {
    const min = s.minute_of_hour != null ? String(s.minute_of_hour).padStart(2, '0') : '00';
    return `每小时 :${min}`;
  }
  if (s.schedule_type === 'cron') {
    return `Cron: ${s.cron_expr || '?'}`;
  }
  return s.schedule_type;
}

function scheduleCard(s) {
  const nextRun = s.next_run_at ? fmtDatetime(s.next_run_at) : (s.enabled ? '计算中' : '已暂停');
  const lastRun = s.last_run_at ? fmtDatetime(s.last_run_at) : '从未执行';
  const enabledClass = s.enabled ? 'status-badge running' : 'status-badge killed';
  const enabledLabel = s.enabled ? '运行中' : '已暂停';
  const mode = s.execution_mode || 'inherit';
  const modeLabel = mode === 'ephemeral' ? '临时沙箱' : (mode === 'persistent' ? '持久容器' : '跟随项目');
  const isWorkflow = (s.target_type || 'task') === 'workflow';
  const targetLabel = isWorkflow
    ? (scheduleTemplatesCache.find(t => t.id === s.template_id)?.name || s.template_id || '工作流')
    : (s.project_name || s.account || '未指定目标');
  const runLink = s.last_run_type === 'workflow' && s.last_workflow_run_id
    ? `<a href="#" class="inline-link" onclick="openScheduleLastTask('${s.id}');return false">${s.last_workflow_run_id.slice(-8)}</a>`
    : (s.last_task_id
      ? `<a href="#" class="inline-link" onclick="openScheduleLastTask('${s.id}');return false">${s.last_task_id.slice(-8)}</a>`
      : '—');
  const typeLabel = isWorkflow ? '工作流' : modeLabel;

  return `
<div class="account-card" id="sched-card-${s.id}">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:8px">
    <div style="flex:1;min-width:0">
      <div style="font-weight:700;font-size:14px;margin-bottom:3px;word-break:break-word">${esc(s.name)}</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
        <span class="type-badge">${schedulePatternLabel(s)}</span>
        <span class="type-badge" style="background:var(--surface-3)">${esc(targetLabel)}</span>
        <span class="type-badge" style="background:var(--surface-3)">${esc(typeLabel)}</span>
        <span class="${enabledClass}">${enabledLabel}</span>
      </div>
    </div>
  </div>
  <div class="account-meta" style="font-size:12px;color:var(--text-3);margin-bottom:10px">
    <div style="display:flex;gap:4px"><span style="color:var(--text-2)">下次执行：</span>${esc(nextRun)}</div>
    <div style="display:flex;gap:4px"><span style="color:var(--text-2)">上次执行：</span>${lastRun}</div>
    <div style="display:flex;gap:4px"><span style="color:var(--text-2)">最近运行：</span>${runLink}</div>
    <div style="display:flex;gap:4px;margin-top:2px;color:var(--text-3);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(s.prompt)}">${esc(s.prompt.slice(0, 80))}${s.prompt.length > 80 ? '…' : ''}</div>
  </div>
  <div style="display:flex;gap:6px;flex-wrap:wrap">
    <button class="btn" onclick="toggleSchedule('${s.id}')" style="font-size:11px">${s.enabled ? '暂停' : '启用'}</button>
    <button class="btn primary" onclick="runScheduleNow('${s.id}')" style="font-size:11px">立即执行</button>
    <button class="btn" onclick="openScheduleEditor('${s.id}')" style="font-size:11px">编辑</button>
    <button class="btn danger" onclick="deleteSchedule('${s.id}')" style="font-size:11px">删除</button>
  </div>
</div>`;
}

function fmtDatetime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch { return iso; }
}

// ── 编辑器弹窗 ────────────────────────────────────────────

async function openScheduleEditor(schedId = null) {
  editingScheduleId = schedId;
  const modal = document.getElementById('schedule-modal');
  document.getElementById('schedule-modal-title').textContent = schedId ? '编辑定时计划' : '新建定时计划';
  document.getElementById('sched-msg').style.display = 'none';
  modal.style.display = '';

  const s = schedId ? schedulesCache.find(x => x.id === schedId) : null;
  await _populateScheduleProjectSelect(s?.project_name);
  await _populateScheduleAccountSelect(s?.account);
  await _populateScheduleTemplateSelect(s?.template_id);

  if (s) {
    _fillScheduleForm(s);
  } else {
    _resetScheduleForm();
  }
  _updateScheduleTypeUI();
  _updateScheduleTargetUI();
}

function _fillScheduleForm(s) {
  document.getElementById('sched-name').value = s.name || '';
  document.getElementById('sched-prompt').value = s.prompt || '';
  document.getElementById('sched-target-type').value = s.target_type || 'task';
  document.getElementById('sched-template').value = s.template_id || '';
  document.getElementById('sched-workflow-input').value = s.workflow_input || s.prompt || '';
  document.getElementById('sched-default-project').value = s.default_project || '';
  document.getElementById('sched-default-account').value = s.default_account || '';
  document.getElementById('sched-workspace-policy').value = s.workspace_policy || 'isolated';
  document.getElementById('sched-project').value = s.project_name || '';
  document.getElementById('sched-account').value = s.account || '';
  document.getElementById('sched-auto').checked = !!s.auto;
  document.getElementById('sched-enabled').checked = s.enabled !== false;
  document.getElementById('sched-type').value = s.schedule_type || 'daily';
  document.getElementById('sched-execution-mode').value = s.execution_mode || 'inherit';
  document.getElementById('sched-output-dir').value = s.output_dir || '';
  document.getElementById('sched-ephemeral-retention').value = s.ephemeral_retention || 'release_on_finish';
  document.getElementById('sched-ephemeral-ttl').value = s.ephemeral_ttl_minutes || 120;

  // time_of_day
  const [h, m] = (s.time_of_day || '09:00').split(':');
  document.getElementById('sched-hour').value = h || '09';
  document.getElementById('sched-minute').value = m || '00';

  // days_of_week
  document.querySelectorAll('.sched-day-cb').forEach(cb => {
    cb.checked = (s.days_of_week || []).includes(Number(cb.value));
  });

  // minute_of_hour
  document.getElementById('sched-minute-of-hour').value =
    s.minute_of_hour != null ? String(s.minute_of_hour).padStart(2, '0') : '00';

  // cron_expr
  const cronInput = document.getElementById('sched-cron-expr');
  if (cronInput) cronInput.value = s.cron_expr || '';

  // webhook
  const webhookEnabled = document.getElementById('sched-webhook-enabled');
  if (webhookEnabled) webhookEnabled.checked = !!s.webhook_enabled;
  _updateWebhookUrl(s);
}

function _resetScheduleForm() {
  document.getElementById('sched-name').value = '';
  document.getElementById('sched-prompt').value = '';
  document.getElementById('sched-target-type').value = 'task';
  document.getElementById('sched-template').value = '';
  document.getElementById('sched-workflow-input').value = '';
  document.getElementById('sched-default-project').value = '';
  document.getElementById('sched-default-account').value = '';
  document.getElementById('sched-workspace-policy').value = 'isolated';
  // 不手动 set value：让 select 显示第一个已填充的 option
  const accountSel = document.getElementById('sched-account');
  if (accountSel) accountSel.value = '';
  document.getElementById('sched-auto').checked = true;
  document.getElementById('sched-enabled').checked = true;
  document.getElementById('sched-type').value = 'daily';
  document.getElementById('sched-execution-mode').value = 'inherit';
  document.getElementById('sched-output-dir').value = '';
  document.getElementById('sched-ephemeral-retention').value = 'release_on_finish';
  document.getElementById('sched-ephemeral-ttl').value = '120';
  document.getElementById('sched-hour').value = '09';
  document.getElementById('sched-minute').value = '00';
  document.querySelectorAll('.sched-day-cb').forEach(cb => { cb.checked = false; });
  document.getElementById('sched-minute-of-hour').value = '00';
  const cronInput = document.getElementById('sched-cron-expr');
  if (cronInput) cronInput.value = '';
  const webhookEnabled = document.getElementById('sched-webhook-enabled');
  if (webhookEnabled) webhookEnabled.checked = false;
  _updateWebhookUrl(null);
}

function _updateWebhookUrl(s) {
  const row = document.getElementById('sched-webhook-url-row');
  if (!row) return;
  if (s && s.webhook_enabled && s.webhook_token) {
    const url = `${window.location.origin}/api/webhooks/${s.webhook_token}/trigger`;
    row.style.display = '';
    row.querySelector('.webhook-url-text').textContent = url;
  } else {
    row.style.display = 'none';
  }
}

function closeScheduleModal(event) {
  if (event && event.target !== document.getElementById('schedule-modal')) return;
  document.getElementById('schedule-modal').style.display = 'none';
}

function closeScheduleModalBtn() {
  document.getElementById('schedule-modal').style.display = 'none';
}

function _updateScheduleTypeUI() {
  const type = document.getElementById('sched-type').value;
  document.getElementById('sched-time-row').style.display   = (type === 'daily' || type === 'weekly') ? '' : 'none';
  document.getElementById('sched-days-row').style.display   = type === 'weekly' ? '' : 'none';
  document.getElementById('sched-minute-row').style.display = type === 'hourly' ? '' : 'none';
  const cronRow = document.getElementById('sched-cron-row');
  if (cronRow) cronRow.style.display = type === 'cron' ? '' : 'none';
}

function _updateScheduleTargetUI() {
  const targetType = document.getElementById('sched-target-type')?.value || 'task';
  const isWorkflow = targetType === 'workflow';
  document.querySelectorAll('.sched-task-field').forEach(el => { el.style.display = isWorkflow ? 'none' : ''; });
  document.querySelectorAll('.sched-workflow-field').forEach(el => { el.style.display = isWorkflow ? '' : 'none'; });
}

async function saveSchedule() {
  const name         = document.getElementById('sched-name').value.trim();
  const prompt       = document.getElementById('sched-prompt').value.trim();
  const target_type  = document.getElementById('sched-target-type').value || 'task';
  const template_id  = document.getElementById('sched-template').value;
  const workflow_input = document.getElementById('sched-workflow-input').value.trim();
  const default_project = document.getElementById('sched-default-project').value;
  const default_account = document.getElementById('sched-default-account').value;
  const workspace_policy = document.getElementById('sched-workspace-policy').value || 'isolated';
  const project_name = document.getElementById('sched-project').value;
  const account      = document.getElementById('sched-account').value;
  const auto         = document.getElementById('sched-auto').checked;
  const enabled      = document.getElementById('sched-enabled').checked;
  const schedule_type = document.getElementById('sched-type').value;
  const execution_mode = document.getElementById('sched-execution-mode').value || 'inherit';
  const output_dir     = document.getElementById('sched-output-dir').value.trim();
  const ephemeral_retention = document.getElementById('sched-ephemeral-retention').value || 'release_on_finish';
  const ephemeral_ttl_minutes = parseInt(document.getElementById('sched-ephemeral-ttl').value || '120', 10);
  const hour         = document.getElementById('sched-hour').value;
  const minute       = document.getElementById('sched-minute').value;
  const minuteOfHour = parseInt(document.getElementById('sched-minute-of-hour').value, 10);

  const days_of_week = [];
  document.querySelectorAll('.sched-day-cb:checked').forEach(cb => {
    days_of_week.push(Number(cb.value));
  });

  const msgEl = document.getElementById('sched-msg');
  const showErr = (msg) => {
    msgEl.style.display = '';
    msgEl.className = 'inline-alert';
    msgEl.textContent = msg;
  };

  const cronExprEl = document.getElementById('sched-cron-expr');
  const cron_expr = cronExprEl ? cronExprEl.value.trim() : '';
  const webhookEnabledEl = document.getElementById('sched-webhook-enabled');
  const webhook_enabled = webhookEnabledEl ? webhookEnabledEl.checked : false;

  if (!name) { showErr('请填写计划名称'); return; }
  if (target_type === 'workflow') {
    if (!template_id) { showErr('请选择工作流模板'); return; }
    if (!workflow_input) { showErr('请填写工作流输入内容'); return; }
    if (workspace_policy === 'shared_ephemeral' && !default_project && !default_account) {
      showErr('共享临时沙箱未选择默认项目时，请选择默认账号');
      return;
    }
  } else {
    if (!prompt) { showErr('请填写任务描述（Prompt）'); return; }
    if (!project_name && execution_mode !== 'ephemeral') { showErr('请选择项目，或将执行环境设为临时沙箱'); return; }
    if (!project_name && execution_mode === 'ephemeral' && !account) { showErr('临时沙箱未选择项目时，请选择账号'); return; }
  }
  if (schedule_type === 'weekly' && !days_of_week.length) { showErr('请至少选择一天'); return; }
  if (schedule_type === 'cron' && !cron_expr) { showErr('请填写 Cron 表达式'); return; }

  const body = {
    name,
    prompt: target_type === 'workflow' ? workflow_input : prompt,
    project_name: target_type === 'workflow' ? '' : project_name,
    account: target_type === 'workflow' ? (default_account || null) : (account || null),
    target_type,
    template_id: target_type === 'workflow' ? template_id : '',
    workflow_input: target_type === 'workflow' ? workflow_input : '',
    project_map: {},
    default_project: target_type === 'workflow' ? default_project : '',
    default_account: target_type === 'workflow' ? default_account : '',
    workspace_policy: target_type === 'workflow' ? workspace_policy : 'isolated',
    auto, enabled,
    schedule_type,
    time_of_day: (schedule_type === 'daily' || schedule_type === 'weekly') ? `${hour}:${minute}` : null,
    days_of_week: schedule_type === 'weekly' ? days_of_week : [],
    minute_of_hour: schedule_type === 'hourly' ? (isNaN(minuteOfHour) ? 0 : minuteOfHour) : null,
    cron_expr: schedule_type === 'cron' ? cron_expr : null,
    execution_mode,
    output_dir,
    ephemeral_retention,
    ephemeral_ttl_minutes: isNaN(ephemeral_ttl_minutes) ? 120 : ephemeral_ttl_minutes,
    webhook_enabled,
  };

  const btn = document.getElementById('sched-save-btn');
  btn.disabled = true;
  try {
    const url = editingScheduleId
      ? `${API}/api/schedules/${editingScheduleId}`
      : `${API}/api/schedules`;
    const method = editingScheduleId ? 'PUT' : 'POST';
    const r = await fetch(url, { method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    if (!r.ok) {
      const err = await r.json().catch(() => ({detail: r.statusText}));
      showErr(err.detail || '保存失败');
      return;
    }
    document.getElementById('schedule-modal').style.display = 'none';
    await loadSchedules();
  } catch (e) {
    showErr(e.message);
  } finally {
    btn.disabled = false;
  }
}

// ── 操作函数 ──────────────────────────────────────────────

async function toggleSchedule(schedId) {
  try {
    const r = await fetch(`${API}/api/schedules/${schedId}/toggle`, { method: 'POST' });
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || '操作失败');
    await loadSchedules();
  } catch (e) {
    alert(e.message);
  }
}

async function runScheduleNow(schedId) {
  const s = schedulesCache.find(x => x.id === schedId);
  if (!confirm(`立即执行「${s?.name || schedId}」？`)) return;
  try {
    const r = await fetch(`${API}/api/schedules/${schedId}/run-now`, { method: 'POST' });
    if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || '执行失败');
    const result = await r.json();
    if (result.run_type === 'workflow') {
      alert(`工作流已提交：${result.workflow_run?.id || result.schedule?.last_workflow_run_id || ''}`);
    } else {
      alert(`任务已提交：${result.task?.id || ''}`);
    }
    await loadSchedules();
  } catch (e) {
    alert(e.message);
  }
}

async function deleteSchedule(schedId) {
  const s = schedulesCache.find(x => x.id === schedId);
  if (!confirm(`确认删除「${s?.name || schedId}」？此操作不可恢复。`)) return;
  try {
    const r = await fetch(`${API}/api/schedules/${schedId}`, { method: 'DELETE' });
    if (!r.ok && r.status !== 204) throw new Error('删除失败');
    await loadSchedules();
  } catch (e) {
    alert(e.message);
  }
}

async function openScheduleLastTask(schedId) {
  const s = schedulesCache.find(x => x.id === schedId);
  if (s?.last_run_type === 'workflow' && s.last_workflow_run_id) {
    showPage('workflows');
    openPipeline(s.last_workflow_run_id);
  } else if (s?.last_task_id) {
    openLogModal(s.last_task_id);
  }
}

// ── 初始化弹窗 HTML（在 loadSchedules 调用前注入一次） ──────

function _initScheduleModal() {
  if (document.getElementById('schedule-modal')) return;

  const hourOpts = HOUR_OPTIONS.map(h => `<option value="${h}">${h}</option>`).join('');
  const minuteOpts = MINUTE_OPTIONS.map(m => `<option value="${m}">${m}</option>`).join('');
  const minuteOfHourOpts = Array.from({length: 60}, (_, i) =>
    `<option value="${String(i).padStart(2,'0')}">${String(i).padStart(2,'0')}</option>`
  ).join('');
  const dayCheckboxes = DAY_LABELS.map((label, i) =>
    `<div style="display:flex;align-items:center;gap:5px;font-size:12px">
       <input type="checkbox" class="sched-day-cb" id="sched-day-${i}" value="${i}" style="width:14px;height:14px;accent-color:var(--accent);cursor:pointer;flex-shrink:0">
       <label for="sched-day-${i}" style="margin:0;cursor:pointer;font-weight:400;color:var(--text-1)">${label}</label>
     </div>`
  ).join('');

  const html = `
<div class="modal-backdrop" id="schedule-modal" style="display:none" onclick="closeScheduleModal(event)">
  <div class="modal-sm" onclick="event.stopPropagation()" style="max-width:500px">
    <div class="section-head">
      <div><div class="modal-title" id="schedule-modal-title">新建定时计划</div></div>
      <button class="btn" onclick="closeScheduleModalBtn()">关闭</button>
    </div>
    <div style="display:flex;flex-direction:column;gap:12px">
      <div class="form-group">
        <label>计划名称 *</label>
        <input id="sched-name" class="form-input" placeholder="例如：每日代码审查">
      </div>
      <div class="form-group">
        <label>触发目标</label>
        <select id="sched-target-type" class="form-input" onchange="_updateScheduleTargetUI()">
          <option value="task">普通任务</option>
          <option value="workflow">工作流模板</option>
        </select>
      </div>
      <div class="form-group sched-workflow-field" style="display:none">
        <label>工作流模板 *</label>
        <select id="sched-template" class="form-input"></select>
      </div>
      <div class="form-group sched-workflow-field" style="display:none">
        <label>工作流输入内容 *</label>
        <textarea id="sched-workflow-input" rows="3" placeholder="例如：openclaw/openclaw"></textarea>
      </div>
      <div class="form-group sched-workflow-field" style="display:none">
        <label>默认项目</label>
        <select id="sched-default-project" class="form-input"></select>
      </div>
      <div class="form-group sched-workflow-field" style="display:none">
        <label>默认账号</label>
        <select id="sched-default-account" class="form-input"></select>
      </div>
      <div class="form-group sched-workflow-field" style="display:none">
        <label>工作区策略</label>
        <select id="sched-workspace-policy" class="form-input">
          <option value="isolated">节点独立执行</option>
          <option value="shared_ephemeral">共享临时沙箱</option>
        </select>
      </div>
      <div class="form-group sched-task-field">
        <label>项目</label>
        <select id="sched-project" class="form-input"></select>
      </div>
      <div class="form-group sched-task-field">
        <label>账号</label>
        <select id="sched-account" class="form-input"></select>
      </div>
      <div class="form-group sched-task-field">
        <label>执行环境</label>
        <select id="sched-execution-mode" class="form-input">
          <option value="inherit">跟随项目配置</option>
          <option value="ephemeral">临时沙箱（执行后释放）</option>
          <option value="persistent">持久容器</option>
        </select>
      </div>
      <div class="form-group sched-task-field">
        <label>输出目录</label>
        <input id="sched-output-dir" class="form-input" placeholder="留空使用默认输出目录">
      </div>
      <div class="form-group sched-task-field">
        <label>临时沙箱保留策略</label>
        <select id="sched-ephemeral-retention" class="form-input">
          <option value="release_on_finish">执行完释放</option>
          <option value="keep_until_ttl">保留到 TTL</option>
          <option value="keep_until_manual_close">手动关闭前保留</option>
        </select>
      </div>
      <div class="form-group sched-task-field">
        <label>TTL 分钟</label>
        <input id="sched-ephemeral-ttl" class="form-input" type="number" min="1" max="1440" value="120">
      </div>
      <div class="form-group sched-task-field">
        <label>任务描述（Prompt）*</label>
        <textarea id="sched-prompt" rows="3" placeholder="描述 AI 要完成的任务..."></textarea>
      </div>
      <div class="form-group">
        <label>执行频率</label>
        <select id="sched-type" class="form-input" onchange="_updateScheduleTypeUI()">
          <option value="daily">每天</option>
          <option value="weekly">每周</option>
          <option value="hourly">每小时</option>
          <option value="cron">自定义 Cron</option>
        </select>
      </div>
      <div id="sched-time-row" class="form-group">
        <label>执行时间</label>
        <div style="display:flex;align-items:center;gap:6px">
          <select id="sched-hour" class="form-input" style="width:72px">${hourOpts}</select>
          <span style="color:var(--text-2);font-weight:600">:</span>
          <select id="sched-minute" class="form-input" style="width:72px">${minuteOpts}</select>
        </div>
      </div>
      <div id="sched-days-row" class="form-group" style="display:none">
        <label>执行星期</label>
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:4px">${dayCheckboxes}</div>
      </div>
      <div id="sched-minute-row" class="form-group" style="display:none">
        <label>执行分钟（:MM）</label>
        <select id="sched-minute-of-hour" class="form-input" style="width:80px">${minuteOfHourOpts}</select>
      </div>
      <div id="sched-cron-row" class="form-group" style="display:none">
        <label>Cron 表达式 <span style="color:var(--text-3);font-weight:400;font-size:11px">（分 时 日 月 周，如 <code>*/15 * * * *</code> = 每15分钟）</span></label>
        <input id="sched-cron-expr" class="form-input" placeholder="*/15 * * * *">
      </div>
      <div class="form-group">
        <div style="display:flex;align-items:center;gap:8px;font-size:13px">
          <input type="checkbox" id="sched-webhook-enabled" style="width:16px;height:16px;accent-color:var(--accent);cursor:pointer;flex-shrink:0"
            onchange="_updateWebhookUrl(null)">
          <label for="sched-webhook-enabled" style="margin:0;cursor:pointer;font-weight:400;color:var(--text-1);font-size:13px">启用 Webhook 触发</label>
        </div>
        <div id="sched-webhook-url-row" style="display:none;margin-top:6px">
          <div style="font-size:11px;color:var(--text-3);margin-bottom:3px">Webhook URL（POST 此地址立即触发计划）：</div>
          <div style="display:flex;gap:6px;align-items:center">
            <code class="webhook-url-text" style="font-size:11px;word-break:break-all;flex:1;background:var(--surface-2);padding:4px 8px;border-radius:4px"></code>
            <button class="btn" style="font-size:11px;padding:3px 8px;flex-shrink:0"
              onclick="navigator.clipboard.writeText(document.querySelector('.webhook-url-text').textContent)">复制</button>
          </div>
        </div>
      </div>
      <div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:4px">
        <div style="display:flex;align-items:center;gap:8px;font-size:13px">
          <input type="checkbox" id="sched-auto" checked style="width:16px;height:16px;accent-color:var(--accent);cursor:pointer;flex-shrink:0">
          <label for="sched-auto" style="margin:0;cursor:pointer;font-weight:400;color:var(--text-1);font-size:13px">全自动模式</label>
        </div>
        <div style="display:flex;align-items:center;gap:8px;font-size:13px">
          <input type="checkbox" id="sched-enabled" checked style="width:16px;height:16px;accent-color:var(--accent);cursor:pointer;flex-shrink:0">
          <label for="sched-enabled" style="margin:0;cursor:pointer;font-weight:400;color:var(--text-1);font-size:13px">立即启用</label>
        </div>
      </div>
      <div id="sched-msg" style="display:none"></div>
      <button id="sched-save-btn" class="btn primary" onclick="saveSchedule()">保存</button>
    </div>
  </div>
</div>`;

  document.body.insertAdjacentHTML('beforeend', html);
}

async function _populateScheduleProjectSelect(selectedName) {
  const sel = document.getElementById('sched-project');
  const defSel = document.getElementById('sched-default-project');
  if (!sel && !defSel) return;
  try {
    const projects = await fetch(`${API}/api/projects`).then(r => r.json()).catch(() => []);
    const taskOptions = `<option value="">不指定项目（仅临时沙箱）</option>` +
      projects.map(p => `<option value="${esc(p.name)}"${p.name === selectedName ? ' selected' : ''}>${esc(p.name)}</option>`).join('');
    const defaultOptions = `<option value="">不指定默认项目</option>` +
      projects.map(p => `<option value="${esc(p.name)}">${esc(p.name)} · ${esc(p.account)}</option>`).join('');
    if (sel) sel.innerHTML = taskOptions;
    if (defSel) defSel.innerHTML = defaultOptions;
  } catch {}
}

async function _populateScheduleAccountSelect(selectedName) {
  const sel = document.getElementById('sched-account');
  const defSel = document.getElementById('sched-default-account');
  if (!sel && !defSel) return;
  try {
    const accounts = await fetch(`${API}/api/accounts`).then(r => r.json()).catch(() => []);
    const options = accounts.map(a => `<option value="${esc(a.name)}"${a.name === selectedName ? ' selected' : ''}>${esc(a.name)} (${esc(a.type)})</option>`).join('');
    if (sel) sel.innerHTML = `<option value="">跟随项目账号</option>` + options;
    if (defSel) defSel.innerHTML = `<option value="">不指定默认账号</option>` + options;
  } catch {}
}

async function _populateScheduleTemplateSelect(selectedId) {
  const sel = document.getElementById('sched-template');
  if (!sel) return;
  try {
    const templates = await fetch(`${API}/api/workflow-templates`).then(r => r.json()).catch(() => []);
    scheduleTemplatesCache = templates;
    sel.innerHTML = `<option value="">选择工作流模板</option>` +
      templates.map(t => `<option value="${esc(t.id)}"${t.id === selectedId ? ' selected' : ''}>${esc(t.name)}</option>`).join('');
  } catch {}
}

// 入口：页面切换到 schedules 时调用
async function initSchedulesPage() {
  _initScheduleModal();
  await _populateScheduleTemplateSelect('');
  await loadSchedules();
}
