
// ══════════════════════════════════════════════════════════════
//  日志模态框
// ══════════════════════════════════════════════════════════════
async function openLogModal(taskId) {
  currentTaskId = taskId;
  currentTaskData = null;
  followMode = false;
  stopFollow();

  document.getElementById('log-modal').style.display = 'flex';
  document.getElementById('log-content').innerHTML = '<div style="color:#3a3a3e;font-size:12px;padding:20px">加载中...</div>';
  document.getElementById('log-meta').innerHTML = '';
  resetLogSummary();
  document.getElementById('kill-btn').style.display = 'none';
  document.getElementById('resume-btn').style.display = 'none';
  setFollowButton(false);

  // 初始化 renderer
  renderer = new ChatLogRenderer(document.getElementById('log-content'), true, true);

  try {
    const [task, logText] = await Promise.all([
      fetch(`${API}/api/tasks/${taskId}`).then(r => r.json()),
      fetch(`${API}/api/tasks/${taskId}/logs`).then(r => r.text()).catch(() => ''),
    ]);
    currentTaskData = task;
    updateLogSummary(task);

    document.getElementById('log-modal-title').textContent = `任务日志 — ${taskId}`;
    document.getElementById('log-meta').innerHTML = `
  <span class="status-dot ${task.status}" style="font-size:12px">${statusLabel(task.status)}</span>
  <span>·</span><span class="badge ${task.type}">${task.type}</span>
  <span>${esc(task.account)}</span>
  <span>·</span><span class="text-muted">${esc(task.project.split('/').pop())}</span>
  <span>·</span><span class="text-muted">${fmtTime(task.created)}</span>`;

    if (task.status === 'running') document.getElementById('kill-btn').style.display = '';
    if (task.conversation_id || task.native_session_id) document.getElementById('resume-btn').style.display = '';

    renderer.render(logText);
    scrollChatToBottom();

    if (task.status === 'running') startFollow();
  } catch (e) {
    document.getElementById('log-content').innerHTML =
      `<div style="color:#f87171;padding:16px">加载失败：${esc(e.message)}</div>`;
  }
}

// ── SSE 跟踪 ──────────────────────────────────────────────
function startFollow() {
  stopFollow();
  followMode = true;
  setFollowButton(true);

  // tail=0: 只推送新内容，避免与初次全量加载重复
  sseSource = new EventSource(`${API}/api/tasks/${currentTaskId}/logs/stream?tail=0`);
  sseSource.onmessage = e => {
    if (e.data === '[DONE]') {
      stopFollow();
      // 刷新任务状态，隐藏终止按钮
      fetch(`${API}/api/tasks/${currentTaskId}`).then(r => r.json()).then(t => {
        if (t.status !== 'running') document.getElementById('kill-btn').style.display = 'none';
      }).catch(() => { });
      return;
    }
    if (renderer) renderer.push(e.data);
    scrollChatToBottom();
  };
  sseSource.onerror = () => stopFollow();
}

function stopFollow() {
  if (sseSource) { sseSource.close(); sseSource = null; }
  followMode = false;
  setFollowButton(false);
}

function toggleFollow() { followMode ? stopFollow() : startFollow(); }

function setFollowButton(active) {
  const btn = document.getElementById('follow-btn');
  if (!btn) return;
  btn.innerHTML = active
    ? '<span aria-hidden="true" id="follow-icon">■</span><span>停止</span>'
    : '<span aria-hidden="true" id="follow-icon">▶</span><span>跟踪</span>';
}

function resetLogSummary() {
  setText('log-summary-status', '-');
  setText('log-summary-account', '-');
  setText('log-summary-project', '-');
  setText('log-summary-created', '-');
}

function updateLogSummary(task) {
  setText('log-summary-status', statusLabel(task.status));
  setText('log-summary-account', `${task.type} · ${task.account}`);
  setText('log-summary-project', task.project ? task.project.split('/').pop() : '-');
  setText('log-summary-created', fmtTime(task.created));
}

function scrollChatToBottom() {
  const panel = document.getElementById('log-panel');
  if (panel) panel.scrollTop = panel.scrollHeight;
}

function closeLogModal(e) {
  if (e && e.target !== document.getElementById('log-modal')) return;
  stopFollow();
  document.getElementById('log-modal').style.display = 'none';
  currentTaskId = null; currentTaskData = null;
}

// ── 续接任务 ──────────────────────────────────────────────
async function resumeCurrentTask() {
  if (!currentTaskData) return;
  const { id: taskId, conversation_id: convId, native_session_id: nativeId } = currentTaskData;

  if (convId) {
    closeLogModal();
    await openTaskSubmitPanel({ mode: 'resume', conversationId: convId });
    return;
  }
  if (nativeId) {
    const name = prompt('该任务尚未加入任务链。请输入新任务链名称：', `续接-${taskId.slice(0, 8)}`);
    if (!name?.trim()) return;
    const btn = document.getElementById('resume-btn');
    btn.disabled = true; btn.textContent = '创建中...';
    try {
      const r = await fetch(`${API}/api/conversations`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), task_id: taskId }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || r.statusText);
      closeLogModal();
      await openTaskSubmitPanel({ mode: 'resume', conversationId: data.id });
    } catch (e) { alert('创建任务链失败：' + e.message); }
    finally { btn.disabled = false; btn.textContent = '续接任务'; }
    return;
  }
  alert('该任务没有可用的会话 ID，无法续接。');
}
