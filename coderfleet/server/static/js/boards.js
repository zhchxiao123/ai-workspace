const BOARD_COLUMNS = [
  { id: 'planned', label: '待规划' },
  { id: 'todo', label: '待执行' },
  { id: 'running', label: '执行中' },
  { id: 'review', label: '待验收' },
  { id: 'done', label: '已完成' },
  { id: 'blocked', label: '受阻' },
  { id: 'parked', label: '已搁置' },
];

function boardColumnLabel(status) {
  return BOARD_COLUMNS.find(c => c.id === status)?.label || status || '未知';
}

async function loadBoards() {
  const shell = document.getElementById('board-shell');
  if (!shell) return;
  shell.innerHTML = '<div class="empty">加载中...</div>';
  try {
    const [boards, projects, tasks] = await Promise.all([
      fetch(`${API}/api/boards`).then(r => r.json()),
      fetch(`${API}/api/projects`).then(r => r.json()).catch(() => []),
      fetch(`${API}/api/tasks?limit=200`).then(r => r.json()).catch(() => []),
    ]);
    projectsCache = projects;
    tasksCache = tasks;
    boardsCache = boards;

    if (!boardsCache.length) {
      const board = await createBoard('开发专题');
      boardsCache = [board];
    }
    if (!activeBoardId || !boardsCache.some(b => b.id === activeBoardId)) {
      activeBoardId = boardsCache[0]?.id || '';
      localStorage.setItem('coderfleet.activeBoardId', activeBoardId);
    }
    renderBoardSelect();
    await loadBoardCards();
  } catch (e) {
    shell.innerHTML = `<div class="empty">加载看板失败：${esc(e.message)}</div>`;
  }
}

function renderBoardSelect() {
  const sel = document.getElementById('board-select');
  if (!sel) return;
  sel.innerHTML = boardsCache.map(b => `<option value="${esc(b.id)}">${esc(b.name)}</option>`).join('');
  sel.value = activeBoardId;
}

function selectBoard(boardId) {
  activeBoardId = boardId;
  localStorage.setItem('coderfleet.activeBoardId', boardId);
  loadBoardCards();
}

async function createBoard(name) {
  const r = await fetch(`${API}/api/boards`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

function openBoardModal() {
  const modal = document.getElementById('board-modal');
  const input = document.getElementById('board-name');
  const msg = document.getElementById('board-msg');
  input.value = '';
  msg.style.display = 'none';
  modal.style.display = '';
  input.focus();
}

function closeBoardModal(e) {
  if (e && e.target !== document.getElementById('board-modal')) return;
  document.getElementById('board-modal').style.display = 'none';
}

async function submitBoard() {
  const name = document.getElementById('board-name').value.trim();
  if (!name) { alert('请填写看板名称'); return; }
  const btn = document.getElementById('board-submit');
  const msg = document.getElementById('board-msg');
  btn.disabled = true;
  msg.style.display = 'none';
  try {
    const board = await createBoard(name);
    boardsCache.unshift(board);
    activeBoardId = board.id;
    localStorage.setItem('coderfleet.activeBoardId', activeBoardId);
    closeBoardModal();
    renderBoardSelect();
    await loadBoardCards();
  } catch (e) {
    msg.style.display = '';
    msg.innerHTML = `<div style="color:var(--red)">创建失败：${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

function createDefaultBoard() {
  openBoardModal();
}

async function deleteActiveBoard() {
  const board = boardsCache.find(b => b.id === activeBoardId);
  if (!board) {
    alert('当前没有可删除的看板');
    return;
  }
  if (!confirm(`确认删除看板「${board.name}」？该看板下的专题卡片也会被删除。`)) return;
  try {
    const r = await fetch(`${API}/api/boards/${activeBoardId}`, { method: 'DELETE' });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(data.detail || r.statusText);
    }
    boardsCache = boardsCache.filter(b => b.id !== activeBoardId);
    activeBoardId = boardsCache[0]?.id || '';
    if (activeBoardId) localStorage.setItem('coderfleet.activeBoardId', activeBoardId);
    else localStorage.removeItem('coderfleet.activeBoardId');
    if (!boardsCache.length) {
      await loadBoards();
      return;
    }
    renderBoardSelect();
    await loadBoardCards();
  } catch (e) {
    alert('删除看板失败：' + e.message);
  }
}

async function loadBoardCards() {
  if (!activeBoardId) {
    document.getElementById('board-shell').innerHTML = '<div class="empty">暂无看板</div>';
    return;
  }
  try {
    boardCardsCache = await fetch(`${API}/api/boards/${activeBoardId}/cards`).then(r => r.json());
    renderBoard();
  } catch (e) {
    document.getElementById('board-shell').innerHTML = `<div class="empty">加载专题失败：${esc(e.message)}</div>`;
  }
}

function taskByIdMap() {
  const map = {};
  (tasksCache || []).forEach(t => { map[t.id] = t; });
  return map;
}

// 卡片作为纯覆盖层：任务从其所指执行单元反查。
// 优先用后端计算好的 task_ids（board_card_id / pipeline_id / conversation_id 的并集），
// 并在前端再兜底匹配一次，保证即使 task_ids 缺失也能显示。
function tasksForBoardCard(card, taskMap) {
  const seen = new Set();
  const result = [];
  (card.task_ids || []).forEach(id => {
    const task = taskMap[id];
    if (task && !seen.has(task.id)) {
      seen.add(task.id);
      result.push(task);
    }
  });
  (tasksCache || []).forEach(task => {
    const match = task.board_card_id === card.id
      || (card.pipeline_id && task.pipeline_id === card.pipeline_id)
      || (card.conversation_id && task.conversation_id === card.conversation_id);
    if (match && !seen.has(task.id)) {
      seen.add(task.id);
      result.push(task);
    }
  });
  result.sort((a, b) => new Date(a.created || 0) - new Date(b.created || 0));
  return result;
}

function renderBoard() {
  const shell = document.getElementById('board-shell');
  const taskMap = taskByIdMap();
  const cardsByStatus = {};
  BOARD_COLUMNS.forEach(col => { cardsByStatus[col.id] = []; });
  boardCardsCache.forEach(card => {
    const bucket = cardsByStatus[card.status] || cardsByStatus.planned;
    bucket.push(card);
  });

  shell.innerHTML = `
    <div class="kanban-board">
      ${BOARD_COLUMNS.map(col => `
        <section class="kanban-column">
          <div class="kanban-column-head">
            <span>${esc(col.label)}</span>
            <span class="kanban-count">${cardsByStatus[col.id].length}</span>
          </div>
          <div class="kanban-card-list">
            ${cardsByStatus[col.id].length ? cardsByStatus[col.id].map(card => renderBoardCard(card, taskMap)).join('') : '<div class="kanban-empty">暂无专题</div>'}
          </div>
        </section>
      `).join('')}
    </div>`;
}

function renderBoardCard(card, taskMap) {
  const tasks = tasksForBoardCard(card, taskMap);
  const taskCount = Math.max((card.task_ids || []).length, tasks.length);
  const latest = tasks[tasks.length - 1] || null;
  const project = card.project_name ? `<span class="board-chip">${esc(card.project_name)}</span>` : '';
  const taskState = latest ? `<span class="status-dot ${latest.status}">${statusLabel(latest.status)}</span>` : '<span class="text-muted text-sm">未提交任务</span>';
  return `
    <article class="kanban-card">
      <div class="kanban-card-top">
        <div class="kanban-card-title">${esc(card.title)}</div>
        <div class="kanban-card-menu">
          <button class="btn ghost" onclick="openBoardCardModal('${esc(card.id)}')">编辑</button>
          <button class="btn ghost danger-text" onclick="archiveBoardCard('${esc(card.id)}')">归档</button>
        </div>
      </div>
      ${card.description ? `<div class="kanban-card-desc">${esc(card.description)}</div>` : ''}
      <div class="kanban-card-meta">
        ${project}
        <span>${taskCount} 个任务</span>
        ${taskState}
      </div>
      <div class="kanban-card-actions">
        <select onchange="moveBoardCard('${esc(card.id)}', this.value)" aria-label="移动专题状态">
          ${BOARD_COLUMNS.map(col => `<option value="${col.id}" ${col.id === card.status ? 'selected' : ''}>${esc(col.label)}</option>`).join('')}
        </select>
        <button class="btn primary" onclick="submitForBoardCard('${esc(card.id)}')">提交任务</button>
      </div>
      ${latest ? `<button class="btn board-log-btn" onclick="openLogModal('${esc(latest.id)}')">查看最近日志 · ${esc(fmtTime(latest.created))}</button>` : ''}
    </article>`;
}

async function moveBoardCard(cardId, status) {
  try {
    const r = await fetch(`${API}/api/board-cards/${cardId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
    const idx = boardCardsCache.findIndex(c => c.id === cardId);
    if (idx >= 0) boardCardsCache[idx] = data;
    renderBoard();
  } catch (e) {
    alert('移动专题失败：' + e.message);
    loadBoardCards();
  }
}

async function openBoardCardModal(cardId = '') {
  if (!activeBoardId) await loadBoards();
  editingBoardCardId = cardId || '';
  const card = editingBoardCardId ? boardCardsCache.find(c => c.id === editingBoardCardId) : null;
  const modal = document.getElementById('board-card-modal');
  document.getElementById('board-card-modal-title').textContent = card ? '编辑开发专题' : '新建开发专题';
  document.getElementById('board-card-submit').textContent = card ? '保存专题' : '创建专题';
  document.getElementById('board-card-title').value = card?.title || '';
  document.getElementById('board-card-description').value = card?.description || '';
  document.getElementById('board-card-msg').style.display = 'none';
  const projectSel = document.getElementById('board-card-project');
  projectSel.innerHTML = '<option value="">不绑定项目</option>' +
    (projectsCache || []).map(p => `<option value="${esc(p.name)}">${esc(p.name)} · ${esc(p.account)}</option>`).join('');
  projectSel.value = card?.project_name || '';
  modal.style.display = '';
  document.getElementById('board-card-title').focus();
}

function closeBoardCardModal(e) {
  if (e && e.target !== document.getElementById('board-card-modal')) return;
  document.getElementById('board-card-modal').style.display = 'none';
  editingBoardCardId = '';
}

async function submitBoardCard() {
  const title = document.getElementById('board-card-title').value.trim();
  if (!title) { alert('请填写专题标题'); return; }
  const btn = document.getElementById('board-card-submit');
  const msg = document.getElementById('board-card-msg');
  btn.disabled = true;
  msg.style.display = 'none';
  try {
    const url = editingBoardCardId
      ? `${API}/api/board-cards/${editingBoardCardId}`
      : `${API}/api/boards/${activeBoardId}/cards`;
    const r = await fetch(url, {
      method: editingBoardCardId ? 'PATCH' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title,
        project_name: document.getElementById('board-card-project').value || '',
        description: document.getElementById('board-card-description').value.trim(),
      }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
    closeBoardCardModal();
    const idx = boardCardsCache.findIndex(c => c.id === data.id);
    if (idx >= 0) boardCardsCache[idx] = data;
    else boardCardsCache.unshift(data);
    renderBoard();
  } catch (e) {
    msg.style.display = '';
    msg.innerHTML = `<div style="color:var(--red)">创建失败：${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

async function archiveBoardCard(cardId) {
  const card = boardCardsCache.find(c => c.id === cardId);
  if (!card) return;
  if (!confirm(`确认归档专题「${card.title}」？`)) return;
  try {
    const r = await fetch(`${API}/api/board-cards/${cardId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ archived: true }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || r.statusText);
    boardCardsCache = boardCardsCache.filter(c => c.id !== cardId);
    renderBoard();
  } catch (e) {
    alert('归档专题失败：' + e.message);
  }
}

async function submitForBoardCard(cardId) {
  const card = boardCardsCache.find(c => c.id === cardId);
  if (!card) return;
  const mode = card.conversation_id ? 'resume' : 'new-chain';
  await openProjectSubmitModal({
    surface: 'board',
    projectName: card.project_name || '',
    boardCardId: card.id,
    conversationId: card.conversation_id || '',
    mode,
  });
  if (!card.conversation_id) {
    document.getElementById('f-conversation-name').value = card.title;
  }
}
