// ── 提交页辅助 ────────────────────────────────────────────
// 注意：conversationBelongsToProject 来自 shared/project-utils.js（全局）
function populateConversations(conversations, tasks = [], projectName = "") {
  const sel = document.getElementById('f-conversation');
  const prev = sel.value;
  const runningConvIds = new Set(tasks.filter(t => t.status === 'running' && t.conversation_id).map(t => t.conversation_id));
  const filteredConversations = projectName
    ? conversations.filter(c => c.project_name === projectName || projectsCache.some(p => p.name === projectName && conversationBelongsToProject(c, p)))
    : conversations;
  sel.innerHTML = '<option value="">选择任务链</option>' +
    filteredConversations.map(c => {
      const running = runningConvIds.has(c.id);
      const proj = c.project?.split('/').pop() || '';
      return `<option value="${esc(c.id)}" data-running="${running}" ${running ? 'style="color:var(--red)"' : ''}>
    ${esc(c.name)} · ${esc(c.account)} · ${esc(proj)} · ${fmtTime(c.updated)}${running ? ' [运行中]' : ''}
  </option>`;
    }).join('');
  sel.value = prev;
  checkConvWarning();
}

function populateProjects(projects, lockedProjectName = "") {
  const sel = document.getElementById('f-project');
  const prev = sel.value;
  const filteredProjects = lockedProjectName ? projects.filter(p => p.name === lockedProjectName) : projects;
  sel.innerHTML = '<option value="">选择项目</option>' +
    filteredProjects.map(p => `<option value="${esc(p.name)}">${esc(p.name)} · ${esc(p.account)} · ${esc(p.path.split('/').pop())}</option>`).join('');
  sel.value = lockedProjectName || prev;
}

function moveSubmitPanel(slotId) {
  const panel = document.getElementById('task-submit-panel');
  const slot = document.getElementById(slotId);
  if (panel && slot && panel.parentElement !== slot) slot.appendChild(panel);
  return panel;
}

async function openTaskSubmitPanel(options = {}) {
  submitContext = { surface: 'task', projectName: options.projectName || '', boardCardId: options.boardCardId || '' };
  showPage('tasks');
  const panel = moveSubmitPanel('task-submit-slot');
  document.getElementById('submit-modal').style.display = 'none';
  document.getElementById('submit-panel-close-btn').style.display = '';
  panel.style.display = '';
  await loadAccountOptions();
  if (options.mode) switchSubmitMode(options.mode);
  if (options.projectName) document.getElementById('f-project').value = options.projectName;
  if (options.conversationId) document.getElementById('f-conversation').value = options.conversationId;
  if (options.focus !== false) document.getElementById('f-prompt').focus();
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function openProjectSubmitModal(options = {}) {
  submitContext = { surface: options.surface || 'project', projectName: options.projectName || projectContext?.name || '', boardCardId: options.boardCardId || '' };
  const panel = moveSubmitPanel('submit-modal-slot');
  const modal = document.getElementById('submit-modal');
  const subtitle = document.getElementById('submit-modal-subtitle');
  subtitle.textContent = submitContext.projectName ? `项目：${submitContext.projectName}` : '当前项目上下文';
  document.getElementById('submit-panel-close-btn').style.display = 'none';
  panel.style.display = '';
  modal.style.display = '';
  await loadAccountOptions();
  switchSubmitMode(options.mode || 'one-off');
  if (options.projectName) document.getElementById('f-project').value = options.projectName;
  if (options.conversationId) document.getElementById('f-conversation').value = options.conversationId;
  if (options.focus !== false) document.getElementById('f-prompt').focus();
}

function closeTaskSubmitPanel() {
  document.getElementById('task-submit-panel').style.display = 'none';
}

function closeSubmitModal(e) {
  if (e && e.target !== document.getElementById('submit-modal')) return;
  document.getElementById('submit-modal').style.display = 'none';
  submitContext = { surface: 'task', projectName: '', boardCardId: '' };
  const panel = moveSubmitPanel('task-submit-slot');
  document.getElementById('submit-panel-close-btn').style.display = '';
  panel.style.display = 'none';
}

function closeSubmitSurface() {
  if (document.getElementById('submit-modal').style.display !== 'none') closeSubmitModal();
  else closeTaskSubmitPanel();
}

async function loadAccountOptions() {
  try {
    const [accounts, convs, projects, tasks] = await Promise.all([
      fetch(`${API}/api/accounts`).then(r => r.json()),
      fetch(`${API}/api/conversations`).then(r => r.json()).catch(() => []),
      fetch(`${API}/api/projects`).then(r => r.json()).catch(() => []),
      fetch(`${API}/api/tasks?limit=100`).then(r => r.json()).catch(() => []),
    ]);
    projectsCache = projects;
    populateAccountFilters(accounts);
    populateConversations(convs, tasks, submitContext.projectName);
    populateProjects(projects, submitContext.projectName);
    const projectGroup = document.getElementById('group-project');
    projectGroup.style.display = submitContext.projectName ? 'none' : projectGroup.style.display;
  } catch { }
}

function switchSubmitMode(mode) {
  document.querySelectorAll('input[name="submit-mode"]').forEach(r => r.checked = r.value === mode);
  document.getElementById('group-project').style.display = (!submitContext.projectName && (mode === 'one-off' || mode === 'new-chain')) ? '' : 'none';
  document.getElementById('group-conversation').style.display = mode === 'resume' ? '' : 'none';
  document.getElementById('group-conversation-name').style.display = mode === 'new-chain' ? '' : 'none';
  if (mode === 'resume') checkConvWarning();
}

function checkConvWarning() {
  const sel = document.getElementById('f-conversation');
  const opt = sel.options[sel.selectedIndex];
  const warn = document.getElementById('conversation-warning');
  const btn = document.getElementById('submit-btn');
  const running = opt?.getAttribute('data-running') === 'true';
  warn.style.display = running ? '' : 'none';
  if (!btn.classList.contains('submitting')) btn.disabled = running;
}

document.getElementById('f-conversation').addEventListener('change', checkConvWarning);

// ── 提交任务 ──────────────────────────────────────────────
async function submitTask() {
  const prompt = document.getElementById('f-prompt').value.trim();
  if (!prompt) { alert('请填写任务描述'); return; }

  const mode = document.querySelector('input[name="submit-mode"]:checked').value;
  let conversationId = null, conversationName = null, projectName = null;

  if (mode === 'one-off') {
    projectName = document.getElementById('f-project').value || null;
    if (!projectName) { alert('请选择项目'); return; }
  } else if (mode === 'resume') {
    conversationId = document.getElementById('f-conversation').value || null;
    if (!conversationId) { alert('请选择任务链'); return; }
    const opt = document.getElementById('f-conversation').options[document.getElementById('f-conversation').selectedIndex];
    if (opt?.getAttribute('data-running') === 'true') { alert('该任务链有任务运行中！'); return; }
  } else if (mode === 'new-chain') {
    projectName = document.getElementById('f-project').value || null;
    conversationName = document.getElementById('f-conversation-name').value.trim() || null;
    if (!projectName) { alert('请选择项目'); return; }
    if (!conversationName) { alert('请填写任务链名称'); return; }
  }

  const btn = document.getElementById('submit-btn');
  const msg = document.getElementById('submit-msg');
  btn.disabled = true; btn.classList.add('submitting'); btn.textContent = '提交中...';
  msg.style.display = 'none';

  try {
    const r = await fetch(`${API}/api/tasks`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, project_name: projectName, auto: document.getElementById('f-auto').checked, conversation_id: conversationId, conversation_name: conversationName, board_card_id: submitContext.boardCardId || null }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
    msg.style.display = '';
    msg.innerHTML = `<div style="color:var(--green);font-weight:bold">已提交：${esc(data.id)}</div>
  <div style="margin-top:8px"><button class="btn primary" onclick="openLogModal('${data.id}')">查看日志</button></div>`;
    resetForm(false);
    loadAccountOptions();
    loadTasks();
    if (currentPage === 'boards' || submitContext.boardCardId) loadBoards();
    if (projectContext) loadProjectsDashboard();
  } catch (e) {
    msg.style.display = '';
    msg.innerHTML = `<div style="color:var(--red)">提交失败：${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false; btn.classList.remove('submitting'); btn.textContent = '提交任务';
  }
}

function resetForm(clearMsg = true) {
  ['f-prompt', 'f-project', 'f-conversation', 'f-conversation-name'].forEach(id => { document.getElementById(id).value = ''; });
  if (submitContext.projectName) document.getElementById('f-project').value = submitContext.projectName;
  document.getElementById('f-auto').checked = true;
  switchSubmitMode('one-off');
  if (clearMsg) document.getElementById('submit-msg').style.display = 'none';
}

// ── 终止任务 ──────────────────────────────────────────────
async function killTask(id) {
  if (!await confirmDialog(`确认终止任务 ${id}？`, { danger: true })) return;
  try { await fetch(`${API}/api/tasks/${id}`, { method: 'DELETE' }); loadTasks(); }
  catch (e) { alert('终止失败：' + e.message); }
}

async function killCurrentTask() {
  if (!currentTaskId) return;
  if (!await confirmDialog(`确认终止任务 ${currentTaskId}？`, { danger: true })) return;
  try { await fetch(`${API}/api/tasks/${currentTaskId}`, { method: 'DELETE' }); closeLogModal(); loadTasks(); }
  catch (e) { alert('终止失败：' + e.message); }
}

async function cleanTasks() {
  if (!await confirmDialog('清理旧记录（保留最近30条）？', { danger: true })) return;
  try {
    const r = await fetch(`${API}/api/tasks/clean`, { method: 'POST' }).then(r => r.json());
    alert(`已清理 ${r.cleaned} 条`); loadTasks();
  } catch (e) { alert('失败：' + e.message); }
}
