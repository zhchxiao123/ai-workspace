// ── AI 对话（聊天室）核心逻辑 ───────────────────────────────────

// 加载会话列表及数据
async function loadConversations(renderWorkspace = true) {
  try {
    const [convs, projects, tasks] = await Promise.all([
      fetch(`${API}/api/conversations`).then(r => r.json()),
      fetch(`${API}/api/projects`).then(r => r.json()).catch(() => []),
      fetch(`${API}/api/tasks?limit=1000`).then(r => r.json()).catch(() => []),
    ]);
    projectsCache = projects;
    tasksCache = tasks;
    chatConversationsList = convs;

    conversationsCache = {};
    convs.forEach(c => { conversationsCache[c.id] = c.name; });
    syncConversationTabLabels();
    renderTopbarTabs();

    renderConversations(convs, projects, tasks);

    if (renderWorkspace) {
      if (!activeConversationId) {
        renderEmptyChatState();
        syncBottomTerminalProjectFromContext();
      } else if (activeConversationId.startsWith('task-')) {
        const taskId = activeConversationId.replace('task-', '');
        const task = tasks.find(t => t.id === taskId);
        if (task) {
          const virtualConv = {
            id: `task-${task.id}`,
            name: task.prompt,
            project: task.project,
            project_name: projects.find(p => taskBelongsToProject(task, p))?.name || '未配置项目',
            account: task.account,
            updated: task.created,
            isOneOff: true
          };
          renderChatWorkspace(virtualConv);
          syncBottomTerminalProjectFromContext();
        } else {
          startNewChat();
        }
      } else {
        const active = convs.find(c => c.id === activeConversationId);
        if (active) {
          renderChatWorkspace(active);
          syncBottomTerminalProjectFromContext();
        } else {
          startNewChat();
        }
      }
    }
  } catch (e) {
    document.getElementById('chat-history-list').innerHTML = `<div class="empty" style="padding: 20px 0;">加载失败: ${esc(e.message)}</div>`;
  }
}

// 以项目大标题分组渲染会话列表
function renderConversations(convs, projects, tasks) {
  const list = document.getElementById('chat-history-list');
  if (!projects.length) {
    list.innerHTML = `<div class="empty" style="padding: 20px 0;">暂无项目配置</div>`;
    return;
  }

  const folderSvg = `<svg class="proj-folder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>`;
  let html = '';

  const sortedProjects = [...projects];
  if (chatProjectSortOrder === 'name') {
    sortedProjects.sort((a, b) => a.name.localeCompare(b.name));
  } else if (chatProjectSortOrder === 'activity') {
    const latestTime = proj => {
      const projConvs = convs.filter(c => conversationBelongsToProject(c, proj));
      const projTasks = tasks.filter(t => taskBelongsToProject(t, proj));
      const times = [
        ...projConvs.map(c => new Date(c.updated || c.created || 0)),
        ...projTasks.map(t => new Date(t.created || 0)),
      ];
      return times.length ? Math.max(...times) : 0;
    };
    sortedProjects.sort((a, b) => latestTime(b) - latestTime(a));
  } else if (chatProjectSortOrder === 'running') {
    const runningCount = proj => tasks.filter(t => taskBelongsToProject(t, proj) && t.status === 'running').length;
    sortedProjects.sort((a, b) => runningCount(b) - runningCount(a));
  }
  // 置顶项目始终排在最前
  sortedProjects.sort((a, b) => {
    const ap = chatPinnedProjectNames.has(a.name) ? 0 : 1;
    const bp = chatPinnedProjectNames.has(b.name) ? 0 : 1;
    return ap - bp;
  });

  const q = chatSearchQuery.trim().toLowerCase();

  sortedProjects.forEach(proj => {
    const projConvs = convs.filter(c => conversationBelongsToProject(c, proj));
    const projTasks = tasks.filter(t => !t.conversation_id && taskBelongsToProject(t, proj));

    const items = [];
    projConvs.forEach(c => {
      const convTasks = tasks.filter(t => t.conversation_id === c.id)
        .sort((a, b) => new Date(b.created || 0) - new Date(a.created || 0));
      const latestStatus = convTasks[0]?.status || 'done';
      items.push({
        type: 'conversation',
        id: c.id,
        name: c.name || c.id,
        time: c.updated || c.created || 0,
        status: latestStatus,
      });
    });
    projTasks.forEach(t => {
      items.push({
        type: 'one-off',
        id: `task-${t.id}`,
        name: t.prompt,
        time: t.created || 0,
        status: t.status || 'done',
      });
    });

    items.sort((a, b) => new Date(b.time) - new Date(a.time));

    // 搜索过滤
    let filteredItems = items;
    if (q) {
      filteredItems = items.filter(item => item.name.toLowerCase().includes(q));
      const projNameMatch = proj.name.toLowerCase().includes(q);
      if (!projNameMatch && filteredItems.length === 0) return;
    }

    const encodedProjectName = encodeURIComponent(proj.name).replace(/'/g, '%27');
    const isCollapsed = chatCollapsedProjectNames.has(proj.name);
    const isPinned = chatPinnedProjectNames.has(proj.name);

    html += `
  <div class="chat-project-group ${isCollapsed ? 'collapsed' : ''} ${isPinned ? 'pinned' : ''}">
    <div class="chat-project-header" onclick="toggleChatProjectGroup('${encodedProjectName}')" title="${isCollapsed ? '展开项目' : '收起项目'}">
      <div class="proj-header-title" title="${esc(proj.name)}">
        <span class="proj-collapse-icon">${isCollapsed ? '+' : '-'}</span>
        ${folderSvg}
        <span>${esc(proj.name)}</span>
        ${isPinned ? `<span class="proj-pin-dot" title="已置顶"></span>` : ''}
      </div>
      <div class="proj-header-actions">
        <button class="proj-dots-btn" onclick="event.stopPropagation(); openProjMenu(event, '${encodedProjectName}', ${isPinned})" title="更多操作" aria-label="更多操作">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>
        </button>
        <button class="proj-new-chat-btn" onclick="event.stopPropagation(); startNewChat({ projectName: '${esc(proj.name)}' })" title="新建对话" aria-label="新建对话">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
            <path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
          </svg>
        </button>
      </div>
    </div>
    <div class="chat-project-items">
`;

    const isExpanded = chatExpandedProjectNames.has(proj.name);
    const visibleItems = isExpanded ? filteredItems : filteredItems.slice(0, CHAT_PROJECT_VISIBLE_LIMIT);
    const hiddenCount = Math.max(0, filteredItems.length - visibleItems.length);

    if (isCollapsed) {
      html += '';
    } else if (filteredItems.length === 0) {
      html += `<div class="chat-project-empty">暂无对话</div>`;
    } else {
      html += visibleItems.map(item => {
        const isActive = item.id === activeConversationId;
        const displayTime = fmtTimeFriendly(item.time);
        const isRunning = item.status === 'running';
        const isPending = item.status === 'pending' || item.status === 'scheduled';
        const statusBadge = isRunning
          ? `<span class="session-status-dot running" title="运行中"></span>`
          : isPending
          ? `<span class="session-status-dot pending" title="${item.status === 'scheduled' ? '已定时' : '排队中'}"></span>`
          : `<span class="session-time">${esc(displayTime)}</span>`;
        return `
      <div class="chat-session-item ${isActive ? 'active' : ''}" data-item-id="${esc(item.id)}" onclick="selectConversation('${esc(item.id)}')">
        <span class="session-name" title="${esc(item.name)}">${esc(item.name)}</span>
        ${statusBadge}
        <button class="session-dots-btn" onclick="event.stopPropagation(); openSessionMenu(event, '${esc(item.id)}', '${item.type}')" title="更多操作" aria-label="更多操作">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>
        </button>
      </div>`;
      }).join('');

      if (filteredItems.length > CHAT_PROJECT_VISIBLE_LIMIT) {
        html += `
      <button class="chat-project-expand-btn" onclick="event.stopPropagation(); toggleChatProjectItems('${encodedProjectName}')">
        ${isExpanded ? '收起' : `展开显示 ${hiddenCount} 个`}
      </button>`;
      }
    }

    html += `
    </div>
  </div>
`;
  });

  list.innerHTML = html;
}

function rerenderChatProjectList() {
  renderConversations(
    chatConversationsList,
    projectsCache,
    tasksCache,
  );
}

function saveChatCollapsedState() {
  try {
    localStorage.setItem('coderfleet.chatCollapsedProjects', JSON.stringify([...chatCollapsedProjectNames]));
  } catch { }
}

function toggleChatProjectGroup(encodedProjectName) {
  const projectName = decodeURIComponent(encodedProjectName);
  if (chatCollapsedProjectNames.has(projectName)) {
    chatCollapsedProjectNames.delete(projectName);
  } else {
    chatCollapsedProjectNames.add(projectName);
  }
  saveChatCollapsedState();
  rerenderChatProjectList();
}

function toggleChatProjectPin(encodedProjectName) {
  const projectName = decodeURIComponent(encodedProjectName);
  if (chatPinnedProjectNames.has(projectName)) {
    chatPinnedProjectNames.delete(projectName);
  } else {
    chatPinnedProjectNames.add(projectName);
  }
  try {
    localStorage.setItem('coderfleet.chatPinnedProjects', JSON.stringify([...chatPinnedProjectNames]));
  } catch { }
  rerenderChatProjectList();
}

function handleChatSearch(value) {
  chatSearchQuery = value;
  rerenderChatProjectList();
}

// ── 全局浮动 context menu ─────────────────────────────────
let _ctxMenuEl = null;
let _ctxMenuCleanup = null;

function openCtxMenu(anchor, items) {
  closeCtxMenu();

  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.innerHTML = items.map((item, i) =>
    item.sep
      ? `<div class="ctx-menu-sep"></div>`
      : `<button class="ctx-menu-item${item.danger ? ' danger' : ''}" data-idx="${i}">
           <span class="ctx-menu-icon">${item.icon}</span>
           <span>${item.label}</span>
         </button>`
  ).join('');

  document.body.appendChild(menu);
  _ctxMenuEl = menu;

  // 定位：优先显示在锚点下方，空间不足时翻转到上方
  const rect = anchor.getBoundingClientRect();
  const menuW = 190;
  let left = rect.right - menuW;
  let top = rect.bottom + 6;
  if (left < 8) left = 8;
  if (top + 220 > window.innerHeight) top = rect.top - 6 - menu.offsetHeight;
  menu.style.left = left + 'px';
  menu.style.top = top + 'px';
  // 再次取高度（渲染后）
  requestAnimationFrame(() => {
    if (!_ctxMenuEl) return;
    const h = menu.offsetHeight;
    if (parseFloat(menu.style.top) + h > window.innerHeight - 8) {
      menu.style.top = (rect.top - 6 - h) + 'px';
    }
  });

  // 绑定每一项
  menu.querySelectorAll('.ctx-menu-item').forEach(btn => {
    const idx = parseInt(btn.dataset.idx, 10);
    btn.addEventListener('click', e => {
      e.stopPropagation();
      closeCtxMenu();
      items[idx].action();
    });
  });

  // 点击外部关闭
  const outsideHandler = e => { if (_ctxMenuEl && !_ctxMenuEl.contains(e.target)) closeCtxMenu(); };
  const escHandler = e => { if (e.key === 'Escape') closeCtxMenu(); };
  setTimeout(() => {
    document.addEventListener('click', outsideHandler);
    document.addEventListener('keydown', escHandler);
  }, 0);
  _ctxMenuCleanup = () => {
    document.removeEventListener('click', outsideHandler);
    document.removeEventListener('keydown', escHandler);
  };
}

function closeCtxMenu() {
  if (_ctxMenuEl) { _ctxMenuEl.remove(); _ctxMenuEl = null; }
  if (_ctxMenuCleanup) { _ctxMenuCleanup(); _ctxMenuCleanup = null; }
}

// SVG 图标片段
const _menuIcons = {
  pin:     `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="17" x2="12" y2="22"/><path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24z"/></svg>`,
  unpin:   `<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M16 2l-1.5 1.5L13 2 8 7l-.5 3.5L4 14l2 2 3-1 4.5 4.5L14 21l3.5-3.5-.5-3.5L22 9l-1.5-1.5L22 6z"/></svg>`,
  rename:  `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`,
  newchat: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
  edit:    `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>`,
  archive: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/></svg>`,
  trash:   `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/></svg>`,
};

function openProjMenu(event, encodedProjectName, isPinned) {
  const projectName = decodeURIComponent(encodedProjectName);
  openCtxMenu(event.currentTarget, [
    {
      label: isPinned ? '取消置顶' : '置顶项目',
      icon: isPinned ? _menuIcons.unpin : _menuIcons.pin,
      action: () => toggleChatProjectPin(encodedProjectName),
    },
    {
      label: '编辑项目',
      icon: _menuIcons.edit,
      action: () => { showPage('projects'); openProjectFormModal(projectName); },
    },
  ]);
}

function openSessionMenu(event, itemId, itemType) {
  const items = [];
  if (itemType === 'conversation') {
    items.push({ label: '重命名', icon: _menuIcons.rename, action: () => inlineRenameConversation(itemId) });
    items.push({ sep: true });
    items.push({ label: '归档', icon: _menuIcons.archive, action: () => archiveConversation(itemId) });
    items.push({ label: '删除', icon: _menuIcons.trash, danger: true, action: () => deleteConversation(itemId) });
  } else {
    items.push({ label: '归档', icon: _menuIcons.archive, action: () => archiveOneOff(itemId) });
    items.push({ label: '删除', icon: _menuIcons.trash, danger: true, action: () => deleteOneOff(itemId) });
  }
  openCtxMenu(event.currentTarget, items);
}

async function inlineRenameConversation(convId) {
  const itemEl = document.querySelector(`.chat-session-item[data-item-id="${CSS.escape(convId)}"]`);
  if (!itemEl) return;
  const nameSpan = itemEl.querySelector('.session-name');
  if (!nameSpan) return;

  const oldName = nameSpan.textContent;
  const input = document.createElement('input');
  input.type = 'text';
  input.value = oldName;
  input.className = 'session-rename-input';
  nameSpan.replaceWith(input);
  input.focus();
  input.select();

  const commit = async () => {
    const newName = input.value.trim();
    input.removeEventListener('blur', commit);
    if (!newName || newName === oldName) {
      rerenderChatProjectList();
      return;
    }
    try {
      const r = await fetch(`${API}/api/conversations/${encodeURIComponent(convId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName }),
      });
      if (r.ok) {
        const conv = chatConversationsList.find(c => c.id === convId);
        if (conv) {
          conv.name = newName;
          conversationsCache[convId] = newName;
        }
      }
    } catch { }
    rerenderChatProjectList();
  };

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { rerenderChatProjectList(); }
  });
  input.addEventListener('blur', commit);
}

const CHAT_SORT_CYCLE = ['default', 'activity', 'name', 'running'];
const CHAT_SORT_LABELS = { default: '默认', activity: '活跃', name: '名称', running: '运行中' };

function _updateSortBtnLabel(order) {
  const btn = document.getElementById('chat-sort-btn');
  const label = document.getElementById('chat-sort-label');
  const text = CHAT_SORT_LABELS[order] || '默认';
  if (label) label.textContent = text;
  if (btn) btn.title = `切换排序方式（当前：${text}）`;
}

function initChatSortBtn() {
  _updateSortBtnLabel(chatProjectSortOrder);
}

function setChatProjectSort(order) {
  chatProjectSortOrder = order;
  localStorage.setItem('coderfleet.chatProjectSort', order);
  _updateSortBtnLabel(order);
  rerenderChatProjectList();
}

function cycleChatProjectSort() {
  const idx = CHAT_SORT_CYCLE.indexOf(chatProjectSortOrder);
  const next = CHAT_SORT_CYCLE[(idx + 1) % CHAT_SORT_CYCLE.length];
  setChatProjectSort(next);
}

function toggleChatProjectItems(encodedProjectName) {
  const projectName = decodeURIComponent(encodedProjectName);
  if (chatExpandedProjectNames.has(projectName)) {
    chatExpandedProjectNames.delete(projectName);
  } else {
    chatExpandedProjectNames.add(projectName);
  }
  rerenderChatProjectList();
}

// 选择会话
async function selectConversation(convId) {
  stopChatFollow();
  activeConversationId = convId;
  activeTabId = upsertConversationTab(convId);
  renderTopbarTabs();
  await loadConversations();
}

// 开启新会话
function startNewChat(options = {}) {
  stopChatFollow();
  activeConversationId = null;
  currentChatTaskId = null;
  activeTabId = 'chat';
  chatNewSessionProject = options.projectName || '';
  showPage('chat');
  loadConversations();
}

// 渲染新会话空状态（无历史记录的初始界面）
function renderEmptyChatState() {
  const workspace = document.getElementById('chat-workspace');
  if (!workspace) return;

  const projectLabel = chatNewSessionProject || '未指定项目';
  currentChatProjectName = chatNewSessionProject;
  pendingImages = [];

  workspace.innerHTML = `
<div class="chat-main-header">
  <div class="chat-main-title-area">
    <div class="chat-main-title">新对话</div>
    <div class="chat-main-subtitle">项目: <strong style="color: var(--accent);">${esc(projectLabel)}</strong></div>
  </div>
</div>

<div class="chat-main-viewport" id="chat-viewport">
  <div id="chat-content">
    <div class="empty" style="margin-top: 60px;">输入第一条指令，开始与 AI 结对开发</div>
  </div>
</div>

${buildChatInputHTML('输入您的开发指令... (按 Enter 发送，Shift+Enter 换行)', '发送第一条消息后将自动创建会话链')}
  `;

  const textarea = document.getElementById('chat-input');
  bindChatTextareaEvents(textarea);
  if (textarea) textarea.focus();
}

// 自适应高度及快捷键发送绑定
function bindChatTextareaEvents(textarea) {
  if (!textarea) return;
  textarea.addEventListener('input', () => {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
  });
  textarea.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      if (e.isComposing || e.keyCode === 229) {
        return;
      }
      e.preventDefault();
      sendChatMessage();
    }
  });
  textarea.addEventListener('paste', handleChatPaste);
}

function scrollChatViewportToBottom() {
  const viewport = document.getElementById('chat-viewport');
  if (viewport) {
    viewport.scrollTop = viewport.scrollHeight;
  }
}

// 渲染右侧会话主工作区
async function renderChatWorkspace(conv) {
  const workspace = document.getElementById('chat-workspace');
  if (!workspace) return;

  currentChatProjectName = conv.project_name || conv.project?.split('/').pop() || '';
  pendingImages = [];

  workspace.innerHTML = `
<!-- 会话头部 -->
<div class="chat-main-header">
  <div class="chat-main-title-area">
    <div class="chat-main-title" title="${esc(conv.name)}">${esc(conv.name)}</div>
    <div class="chat-main-subtitle">
      项目: <strong style="color: var(--accent);">${esc(conv.project_name || conv.project?.split('/').pop() || '未知')}</strong> · 
      账号: <span>${esc(conv.account || '未指定')}</span> · 
      活跃: <span>${fmtTime(conv.updated)}</span>
    </div>
  </div>
  <div style="display: flex; gap: 8px;" id="chat-header-actions">
    <!-- 动态渲染当前任务动作 -->
  </div>
</div>

<!-- 对话渲染区 -->
<div class="chat-main-viewport" id="chat-viewport">
  <div id="chat-content"></div>
</div>

<!-- 底部输入框 -->
${buildChatInputHTML('输入您下一轮的指令... (按 Enter 发送，Shift+Enter 换行)', conv.isOneOff ? '一次性单任务 (发送消息升级为任务链)' : 'AI 连续问答 · 会话链模式')}
  `;

  const textarea = document.getElementById('chat-input');
  bindChatTextareaEvents(textarea);

  const chatContent = document.getElementById('chat-content');
  chatContent.innerHTML = '<div style="color:var(--text-3);font-size:12px;padding:20px">正在加载会话历史...</div>';

  chatRenderer = new ChatLogRenderer(chatContent);

  try {
    // 找出该会话下的所有 task，如果是一次性任务，过滤出这单条任务
    const convTasks = conv.isOneOff
      ? tasksCache.filter(t => t.id === conv.id.replace('task-', ''))
      : tasksCache
        .filter(t => t.conversation_id === conv.id)
        .sort((a, b) => new Date(a.created || 0) - new Date(b.created || 0));

    // 渲染头部动作按钮与状态文本
    const headerActions = document.getElementById('chat-header-actions');
    const runningTask = convTasks.find(t => t.status === 'running');
    const pendingTask = convTasks.find(t => t.status === 'pending');
    const scheduledTask = convTasks.find(t => t.status === 'scheduled');

    let actionBtnHtml = '';
    if (runningTask) {
      currentChatTaskId = runningTask.id;
      actionBtnHtml = `<button class="btn danger" onclick="killChatTask('${runningTask.id}')">终止执行</button>`;
    } else if (pendingTask) {
      currentChatTaskId = pendingTask.id;
      actionBtnHtml = `<button class="btn danger" onclick="killChatTask('${pendingTask.id}')">取消排队</button>`;
    } else if (scheduledTask) {
      currentChatTaskId = scheduledTask.id;
      actionBtnHtml = `<button class="btn danger" onclick="killChatTask('${scheduledTask.id}')">取消定时</button>`;
    } else {
      currentChatTaskId = null;
    }

    headerActions.innerHTML = `
      ${actionBtnHtml}
    `;

    const runningCount = convTasks.filter(t => t.status === 'running').length;
    const pendingCount = convTasks.filter(t => t.status === 'pending').length;
    const scheduledCount = convTasks.filter(t => t.status === 'scheduled').length;

    let statusHtml = '';
    if (runningCount > 0) {
      statusHtml = `<span style="color: var(--green); animation: pulse 1.5s infinite;">● AI 正在执行任务...</span>`;
      if (pendingCount > 0) {
        statusHtml += ` <span style="color: var(--text-2); font-size: 11px;">(${pendingCount}个任务在排队...)</span>`;
      }
    } else if (pendingCount > 0) {
      statusHtml = `<span style="color: var(--text-2);">● 任务在排队中 (${pendingCount}个)</span>`;
    } else if (scheduledCount > 0) {
      statusHtml = `<span style="color: var(--text-2);">● 已定时待发送 (${scheduledCount}个)</span>`;
    } else {
      statusHtml = `<span style="color: var(--text-2);">就绪</span>`;
    }
    document.getElementById('chat-status-text').innerHTML = statusHtml;

    if (convTasks.length === 0) {
      chatContent.innerHTML = `<div class="empty">会话目前没有指令记录，发送消息开始交流。</div>`;
      return;
    }

    // 并行获取所有的日志
    const logPromises = convTasks.map(t =>
      fetch(`${API}/api/tasks/${t.id}/logs`)
        .then(r => r.text())
        .catch(() => '')
    );

    const logs = await Promise.all(logPromises);
    chatContent.innerHTML = '';

    // 循环独立渲染每一个任务的提问和日志
    convTasks.forEach((task, idx) => {
      const logText = logs[idx];

      // 1. 渲染用户提问蓝气泡
      const userWrap = document.createElement('div');
      userWrap.className = 'timeline-node-wrapper user-wrapper';
      userWrap.innerHTML = `
    <div class="user-bubble">
      <div class="user-bubble-title-row">
        <div class="user-bubble-title">你:</div>
        <button class="user-copy-btn" onclick="copyUserBubble(this)" title="复制">${copyBtnSVG()}</button>
      </div>
      <div class="user-bubble-content">${esc(task.prompt)}</div>
      ${renderTaskImageAttachments(task, currentChatProjectName)}
    </div>`;
      chatContent.appendChild(userWrap);

      // 2. 渲染日志输出的容器
      const logWrap = document.createElement('div');
      logWrap.style.marginBottom = '24px';
      chatContent.appendChild(logWrap);

      // 3. 构建局部的 ChatLogRenderer 并进行渲染
      // 每一个任务在渲染时，均传入 foldProcess=true，把中间执行步骤折叠起来，把回复直接展现
      const localRenderer = new ChatLogRenderer(logWrap, task.status === 'running', true, task.project_name || null);
      if (task.status === 'pending') {
        localRenderer.renderPending();
      } else if (task.status === 'scheduled') {
        localRenderer.renderScheduled(task.execute_at);
      } else {
        localRenderer.render(logText, task.type);
      }

      // 4. 如果是最后一个任务，把该 localRenderer 赋给全局 chatRenderer 方便 SSE 追加
      if (idx === convTasks.length - 1) {
        chatRenderer = localRenderer;
      }
    });

    scrollChatViewportToBottom();

    // 追踪任务选择：优先正在运行的任务，其次是第一个 pending 任务，最后是第一个 scheduled 任务
    const activeTask = runningTask
      || convTasks.find(t => t.status === 'pending')
      || convTasks.find(t => t.status === 'scheduled');
    if (activeTask) {
      // chatRenderer 始终对应最后一个任务的渲染器。
      // 若 activeTask 就是最后一个任务（最常见情况），把其日志字节数告知服务端，
      // 服务端从该偏移量开始推送，避免重新打开会话时内容重复。
      const lastIdx = convTasks.length - 1;
      const activeIdx = convTasks.indexOf(activeTask);
      const alreadyRenderedLog = activeIdx === lastIdx ? (logs[lastIdx] || '') : '';
      const skipBytes = new TextEncoder().encode(alreadyRenderedLog).byteLength;
      startChatFollow(activeTask.id, skipBytes);
    }
  } catch (e) {
    chatContent.innerHTML = `<div style="color:var(--red);padding:16px">加载会话历史失败: ${esc(e.message)}<br><pre style="font-size:11px;color:var(--text-3);margin-top:8px">${esc(e.stack)}</pre></div>`;
  }
}

// 发送消息及自动升级任务链
async function sendChatMessage() {
  const textarea = document.getElementById('chat-input');
  if (!textarea) return;
  let promptText = textarea.value.trim();
  if (!promptText && pendingImages.length === 0) return;
  if (!promptText && pendingImages.length > 0) {
    promptText = '请查看附件图片。';
  }

  if (chatUploadingImages > 0) {
    alert('图片还在上传中，请稍后再发送');
    return;
  }

  const sendBtn = document.getElementById('chat-send-btn');
  const autoMode = document.getElementById('chat-auto-mode')?.checked || false;

  const schedCheckbox = document.getElementById('chat-schedule-checkbox');
  const schedTimeInput = document.getElementById('chat-sched-time');
  let executeAt = null;
  if (schedCheckbox && schedCheckbox.checked && schedTimeInput && schedTimeInput.value) {
    executeAt = schedTimeInput.value;
    if (executeAt.includes(':') && executeAt.split(':').length === 2) {
      executeAt += ':00';
    }
  }

  let convId = activeConversationId;
  let projectName = null;
  let conversationName = null;

  // 1. 新会话：在提交首条消息时，透明进行：提交 task -> 升级为 conversation 这一完整事务
  if (!convId) {
    projectName = chatNewSessionProject;
    if (!projectName) {
      alert('请先从左侧项目列表选择项目后再开始对话！');
      return;
    }
    conversationName = promptText.substring(0, 20) + (promptText.length > 20 ? '...' : '');

    sendBtn.disabled = true;
    sendBtn.textContent = '建会话...';

    try {
      // 提交第一个任务并直接创建会话
      const rTask = await fetch(`${API}/api/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: promptText,
          project_name: projectName,
          auto: autoMode,
          conversation_name: conversationName,
          images: pendingImages.map(i => i.container_path),
          execute_at: executeAt,
        })
      });
      const taskData = await rTask.json();
      if (!rTask.ok) throw new Error(taskData.detail || rTask.statusText);

      if (!taskData.conversation_id) {
        throw new Error('后端未返回会话 ID');
      }

      activeConversationId = taskData.conversation_id;
      activeTabId = upsertConversationTab(activeConversationId);
      renderTopbarTabs();
      textarea.value = '';
      textarea.style.height = 'auto';
      pendingImages = [];
      renderImagePreviews();

      if (schedCheckbox) schedCheckbox.checked = false;
      toggleSchedTimeInput(false);
      if (schedTimeInput) schedTimeInput.value = '';

      sendBtn.disabled = false;
      sendBtn.textContent = '发送';

      await selectConversation(activeConversationId);
      loadTasks();
      loadProjectsDashboard();
      return;
    } catch (e) {
      alert('开启会话失败: ' + e.message);
      sendBtn.disabled = false;
      sendBtn.textContent = '发送';
      return;
    }
  }

  // 2. 一次性任务升级为正式的任务链会话
  if (convId && convId.startsWith('task-')) {
    const taskId = convId.replace('task-', '');
    const task = tasksCache.find(t => t.id === taskId);
    if (!task) {
      alert('未找到该一次性任务，无法升级会话！');
      return;
    }
    sendBtn.disabled = true;
    sendBtn.textContent = '升级会话...';

    try {
      const rConv = await fetch(`${API}/api/conversations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: task.prompt.substring(0, 15) + (task.prompt.length > 15 ? '...' : ''),
          task_id: taskId
        })
      });
      const convData = await rConv.json();
      if (!rConv.ok) throw new Error(convData.detail || rConv.statusText);

      convId = convData.id;
      activeConversationId = convId;
      activeTabId = upsertConversationTab(convId);
      renderTopbarTabs();
    } catch (e) {
      alert('升级任务链失败: ' + e.message);
      sendBtn.disabled = false;
      sendBtn.textContent = '发送';
      return;
    }
  }

  // 3. 正常发送追问消息
  sendBtn.disabled = true;
  sendBtn.textContent = '发送中...';

  try {
    const r = await fetch(`${API}/api/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt: promptText,
        auto: autoMode,
        conversation_id: convId,
        images: pendingImages.map(i => i.container_path),
        execute_at: executeAt,
      })
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);

    textarea.value = '';
    textarea.style.height = 'auto';
    pendingImages = [];
    renderImagePreviews();

    if (schedCheckbox) schedCheckbox.checked = false;
    toggleSchedTimeInput(false);
    if (schedTimeInput) schedTimeInput.value = '';

    sendBtn.disabled = false;
    sendBtn.textContent = '发送';

    await selectConversation(convId);
    loadTasks();
    loadProjectsDashboard();
  } catch (e) {
    alert('发送消息失败: ' + e.message);
    sendBtn.disabled = false;
    sendBtn.textContent = '发送';
  }
}

// 实时日志 SSE 追踪
// skipBytes: 客户端已渲染的字节数，传给服务端 skip_bytes 参数以精确跳过已有内容。
function startChatFollow(taskId, skipBytes = 0) {
  stopChatFollow();
  chatFollowMode = true;
  const qs = skipBytes > 0 ? `skip_bytes=${skipBytes}` : 'tail=0';
  sseChatSource = new EventSource(sseUrl(`${API}/api/tasks/${taskId}/logs/stream?${qs}`));
  sseChatSource.onmessage = e => {
    if (e.data === '[DONE]') {
      stopChatFollow();
      if (activeConversationId) {
        selectConversation(activeConversationId);
      }
      return;
    }
    if (chatRenderer) {
      chatRenderer.push(e.data);
    }
    scrollChatViewportToBottom();
  };
  sseChatSource.onerror = () => stopChatFollow();
}

function stopChatFollow() {
  chatFollowMode = false;
  if (sseChatSource) {
    sseChatSource.close();
    sseChatSource = null;
  }
}

async function archiveConversation(convId) {
  try {
    const r = await fetch(`${API}/api/conversations/${convId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'archived' }),
    });
    if (!r.ok) { const d = await r.json(); throw new Error(d.detail || r.statusText); }
    if (activeConversationId === convId) {
      activeConversationId = null;
      currentChatTaskId = null;
    }
    loadConversations();
  } catch (e) {
    alert('归档失败: ' + e.message);
  }
}

async function deleteConversation(convId) {
  if (!confirm('确定要永久删除该对话吗？任务记录将保留，但对话链不可恢复。')) return;
  try {
    const r = await fetch(`${API}/api/conversations/${convId}`, { method: 'DELETE' });
    if (!r.ok && r.status !== 204) { const d = await r.json(); throw new Error(d.detail || r.statusText); }
    if (activeConversationId === convId) {
      activeConversationId = null;
      currentChatTaskId = null;
    }
    loadConversations();
  } catch (e) {
    alert('删除失败: ' + e.message);
  }
}

async function killChatTask(taskId) {
  if (!confirm('确定要终止当前 AI 任务的执行吗？')) return;
  try {
    const r = await fetch(`${API}/api/tasks/${taskId}`, { method: 'DELETE' });
    if (!r.ok) {
      const data = await r.json();
      throw new Error(data.detail || r.statusText);
    }
    if (activeConversationId) {
      selectConversation(activeConversationId);
    }
    loadTasks();
  } catch (e) {
    alert('终止失败: ' + e.message);
  }
}

async function archiveOneOff(itemId) {
  const taskId = itemId.replace('task-', '');
  try {
    const r = await fetch(`${API}/api/tasks/${taskId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ archived: true }),
    });
    if (!r.ok) { const d = await r.json(); throw new Error(d.detail || r.statusText); }
    if (activeConversationId === itemId) {
      activeConversationId = null;
      currentChatTaskId = null;
    }
    loadConversations();
  } catch (e) {
    alert('归档失败: ' + e.message);
  }
}

async function deleteOneOff(itemId) {
  if (!confirm('确定要永久删除该一次性任务吗？任务记录与日志将全部被清除且不可恢复。')) return;
  const taskId = itemId.replace('task-', '');
  try {
    const r = await fetch(`${API}/api/tasks/${taskId}/record`, { method: 'DELETE' });
    if (!r.ok && r.status !== 204) { const d = await r.json(); throw new Error(d.detail || r.statusText); }
    if (activeConversationId === itemId) {
      activeConversationId = null;
      currentChatTaskId = null;
    }
    loadConversations();
  } catch (e) {
    alert('删除失败: ' + e.message);
  }
}

// ── 图片上传 ──────────────────────────────────────────────

function buildChatInputHTML(placeholder, hint) {
  const attachIcon = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M5 12h14"/></svg>`;
  return `
<div class="chat-input-container">
  <div class="chat-input-composer">
    <div id="chat-image-previews" class="chat-image-preview-row" style="display:none;"></div>
    <textarea class="chat-input-textarea" id="chat-input" placeholder="${placeholder}" rows="1"></textarea>
    <div id="chat-upload-status" class="chat-upload-status" style="display:none;"></div>
    <div class="chat-input-actions">
      <div class="chat-input-options">
        <input type="file" id="chat-file-input" accept="image/*" multiple style="display:none" onchange="handleImageSelect(this)">
        <button class="chat-upload-btn" onclick="document.getElementById('chat-file-input').click()" title="附加图片，或直接粘贴截图">${attachIcon}</button>
        <label class="toggle-row" style="cursor: pointer;">
          <input type="checkbox" id="chat-auto-mode" checked> 全自动模式 (--dangerously-skip-permissions)
        </label>
        <span style="color: var(--border-md);">|</span>
        <label class="toggle-row" style="cursor: pointer;">
          <input type="checkbox" id="chat-schedule-checkbox" onchange="toggleSchedTimeInput(this.checked)"> 定时发送 ⏰
        </label>
        <span id="chat-sched-time-wrap" style="display: none; align-items: center; gap: 4px;">
          <input type="datetime-local" id="chat-sched-time" style="background: var(--surface-2); border: 1px solid var(--border-md); color: var(--text); border-radius: var(--radius); font-size: 11px; padding: 2px 4px; outline: none; min-height: 24px; width: auto;">
        </span>
        <span style="color: var(--border-md);">|</span>
        <span id="chat-status-text" style="color: var(--text-2);">就绪</span>
      </div>
      <div class="chat-input-side">
        <div class="chat-input-hint">${hint}</div>
        <button class="btn primary chat-send-btn" id="chat-send-btn" onclick="sendChatMessage()">发送</button>
      </div>
    </div>
  </div>
</div>`;
}

function toggleSchedTimeInput(checked) {
  const el = document.getElementById('chat-sched-time-wrap');
  if (el) {
    el.style.display = checked ? 'inline-flex' : 'none';
  }
}

async function handleImageSelect(input) {
  const files = Array.from(input.files);
  input.value = '';
  if (!files.length) return;
  await uploadChatImages(files);
}

async function handleChatPaste(event) {
  const items = Array.from(event.clipboardData?.items || []);
  const files = items
    .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
    .map(item => item.getAsFile())
    .filter(Boolean);

  if (!files.length) return;
  event.preventDefault();
  await uploadChatImages(files, true);
}

async function uploadChatImages(files, fromPaste = false) {
  if (!currentChatProjectName) {
    alert('请先从左侧选择项目后再上传图片');
    return;
  }

  // Phase 2: 复用共享的 uploadImagesForCompose（消除与 mobile 的重复 wrapper）
  await uploadImagesForCompose(files, fromPaste, {
    getProjectName: () => currentChatProjectName,
    onItemUploaded: (item) => {
      if (!Array.isArray(pendingImages)) pendingImages = [];
      pendingImages.push(item);
      renderImagePreviews();
    },
    onProgressChange: (count) => {
      chatUploadingImages = count;
      updateChatUploadState();
    },
    onError: (msg) => alert(msg),
    getApiBase: () => (typeof API !== 'undefined' ? API : ''),
  });
}

// normalizeImageFileForUpload 已迁移至 shared/image-upload.js（全局可用）

function updateChatUploadState() {
  const sendBtn = document.getElementById('chat-send-btn');
  const status = document.getElementById('chat-upload-status');
  if (sendBtn) {
    sendBtn.disabled = chatUploadingImages > 0;
    sendBtn.textContent = chatUploadingImages > 0 ? '上传中...' : '发送';
  }
  if (status) {
    status.style.display = chatUploadingImages > 0 ? '' : 'none';
    status.textContent = chatUploadingImages > 0 ? `正在上传 ${chatUploadingImages} 张图片...` : '';
  }
}

// Phase 2 去重：使用共享的 renderPendingPreviews（desktop 配置）
function renderImagePreviews() {
  renderPendingPreviews({
    containerId: 'chat-image-previews',
    thumbClass: 'chat-image-thumb',
    removeClass: 'chat-image-thumb-remove',
    getImages: () => pendingImages,
    removeImage: (index) => pendingImages.splice(index, 1),
    clearImages: () => { pendingImages = []; },
    afterRender: () => {},
    getSseUrl: (u) => sseUrl(u),
    getApi: () => (typeof API !== 'undefined' ? API : ''),
    escFn: (s) => esc(s),
  });
}

// removePendingImage / clearPendingImages 由 shared 提供（全局）
// 它们会自动回退调用 renderImagePreviews（window 上已存在）

// renderTaskImageAttachments 由 shared/image-upload.js 提供（全局可用）
// 直接调用即可，此处无需再定义包装函数（否则会覆盖 window.renderTaskImageAttachments，导致无限递归）
