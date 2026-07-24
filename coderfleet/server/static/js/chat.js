// ── AI 对话（聊天室）核心逻辑 ───────────────────────────────────

// 用首条消息生成会话标题（系统级 LLM，不占池化配额）。未配置时静默跳过，
// 保留调用方已设置的截断兜底名字。失败也静默——命名从来不是关键路径。
async function _autoTitleConversation(convId, promptText) {
  if (!systemLlmConfigured || !promptText.trim()) return;
  try {
    const r = await fetch(`${API}/api/summarize-title`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: promptText }),
    });
    if (!r.ok) return;
    const { title } = await r.json();
    if (!title || !title.trim()) return;
    const rr = await fetch(`${API}/api/conversations/${encodeURIComponent(convId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: title.trim() }),
    });
    if (!rr.ok) return;
    const conv = chatConversationsList.find(c => c.id === convId);
    if (conv) conv.name = title.trim();
    conversationsCache[convId] = title.trim();
    syncConversationTabLabels();
    rerenderChatProjectList();
  } catch { /* 命名失败静默，兜底名字保留 */ }
}

// 加载会话列表及数据
async function loadConversations(renderWorkspace = true) {
  // Restore active conversation from sessionStorage on first load after page refresh
  if (activeConversationId === null && renderWorkspace) {
    try {
      const saved = sessionStorage.getItem('coderfleet.activeConvId');
      if (saved) activeConversationId = saved;
    } catch (_) {}
  }
  try {
    const [convs, projects, tasks] = await Promise.all([
      fetch(`${API}/api/conversations`).then(r => r.json()),
      fetch(`${API}/api/projects`).then(r => r.json()).catch(() => []),
      fetch(`${API}/api/tasks?limit=1000`).then(r => r.json()).catch(() => []),
    ]);
    const shouldRefreshActiveWorkspace = shouldRefreshActiveChatForTaskStatusChange(tasks);
    projectsCache = projects;
    tasksCache = tasks;
    chatConversationsList = convs;

    conversationsCache = {};
    convs.forEach(c => { conversationsCache[c.id] = c.name; });
    syncConversationTabLabels();
    renderTopbarTabs();

    renderConversations(convs, projects, tasks);

    if (!renderWorkspace && shouldRefreshActiveWorkspace && activeConversationId && currentPage === 'chat') {
      selectConversation(activeConversationId);
      return;
    }

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

function shouldRefreshActiveChatForTaskStatusChange(nextTasks) {
  if (!activeConversationId || !Array.isArray(nextTasks) || !Array.isArray(tasksCache)) return false;
  const previousById = new Map(tasksCache.map(t => [t.id, t]));
  return nextTasks.some(task => {
    const previous = previousById.get(task.id);
    if (!previous) return false;

    const belongsToActiveChat = activeConversationId.startsWith('task-')
      ? task.id === activeConversationId.replace('task-', '')
      : task.conversation_id === activeConversationId;
    if (!belongsToActiveChat) return false;

    const wasWaiting = previous.status === 'pending' || previous.status === 'scheduled';
    const isNowRenderable = task.status === 'running' || task.status === 'done' || task.status === 'failed' || task.status === 'killed';
    return wasWaiting && isNowRenderable;
  });
}

// 5 秒轮询入口：先问一次轻量心跳（只有 id/status/conversation_id/finished）。
// 心跳只用来快速刷新侧边栏的运行中角标；但心跳既不带时间戳也不认识心跳到来前
// 才新建的任务 id，所以「当前会话状态变化」「出现心跳未见过的任务 id」「任意任务
// 状态变化」都需要触发一次全量刷新，否则「最近访问」的排序和运行角标会滞后于
// 其它会话/后台任务的实时变化。
async function pollChatHeartbeat() {
  let heartbeat;
  try {
    heartbeat = await fetch(`${API}/api/tasks/heartbeat`).then(r => r.json());
  } catch (_) {
    return;
  }
  if (!Array.isArray(heartbeat)) return;

  const needsActiveWorkspaceRefresh = shouldRefreshActiveChatForTaskStatusChange(heartbeat);

  const byId = new Map(tasksCache.map(t => [t.id, t]));
  let hasNewOrChangedTask = false;
  heartbeat.forEach(h => {
    const existing = byId.get(h.id);
    if (existing) {
      if (existing.status !== h.status || existing.finished !== h.finished) {
        hasNewOrChangedTask = true;
      }
      existing.status = h.status;
      existing.finished = h.finished;
    } else {
      // 心跳里出现了缓存里没有的任务 id：说明有新任务提交了，侧边栏的
      // 「最近访问」需要用一次全量刷新去拿到它所属会话的最新排序时间。
      hasNewOrChangedTask = true;
    }
  });
  if (currentPage === 'chat') renderConversations(chatConversationsList, projectsCache, tasksCache);

  if (needsActiveWorkspaceRefresh || hasNewOrChangedTask) loadConversations(false);
}

// 以项目大标题分组渲染会话列表
function renderConversations(convs, projects, tasks) {
  // Skip sidebar rerender while user is typing in a rename input to prevent accidental auto-save.
  if (_isRenamingConversation) return;
  const list = document.getElementById('chat-history-list');
  const activeQuery = chatSearchQuery.trim();
  if (activeQuery) {
    renderChatSearchResults(chatSearchResults, activeQuery, chatSearchDeep);
    return;
  }
  const activeProjects = projects.filter(p => p.active !== false);
  if (!activeProjects.length) {
    list.innerHTML = `<div class="empty" style="padding: 20px 0;">暂无项目配置</div>`;
    return;
  }

  const folderSvg = `<svg class="proj-folder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>`;
  let html = renderRecentSection(convs, activeProjects, tasks);

  const sortedProjects = [...activeProjects];
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
      const isTerminal = c.mode === 'terminal';
      const convTasks = isTerminal ? [] : tasks.filter(t => t.conversation_id === c.id)
        .sort((a, b) => new Date(b.created || 0) - new Date(a.created || 0));
      const latestStatus = isTerminal ? 'terminal' : (convTasks[0]?.status || 'done');
      items.push({
        type: 'conversation',
        id: c.id,
        name: c.name || c.id,
        time: c.updated || c.created || 0,
        status: latestStatus,
        mode: c.mode || 'chat',
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
        ${proj.ephemeral ? `<span style="font-size:10px;color:var(--accent);opacity:.8;margin-left:4px" title="临时容器项目">⚡</span>` : ''}
        ${isPinned ? `<span class="proj-pin-dot" title="已置顶"></span>` : ''}
      </div>
      <div class="proj-header-actions">
        <button class="proj-dots-btn" onclick="event.stopPropagation(); openProjMenu(event, '${encodedProjectName}', ${isPinned})" title="更多操作" aria-label="更多操作">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>
        </button>
        ${proj.ephemeral ? '' : `<button class="proj-new-chat-btn" onclick="event.stopPropagation(); startNewTerminalConversation('${esc(proj.name)}')" title="新建终端会话" aria-label="新建终端会话">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>
          </svg>
        </button>`}
        <button class="proj-new-chat-btn" onclick="event.stopPropagation(); ${proj.ephemeral ? `openEphemeralModalForProject('${esc(proj.name)}', '${esc(proj.account)}')` : `startNewChat({ projectName: '${esc(proj.name)}' })`}" title="${proj.ephemeral ? '新建临时任务' : '新建对话'}" aria-label="${proj.ephemeral ? '新建临时任务' : '新建对话'}">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            ${proj.ephemeral
              ? '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/>'
              : '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>'}
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
        const isTerminalMode = item.mode === 'terminal';
        const isRunning = item.status === 'running';
        const isPending = item.status === 'pending' || item.status === 'scheduled';
        const modeIcon = isTerminalMode
          ? `<span class="session-mode-icon" title="终端会话">⌨</span>`
          : '';
        const statusBadge = isTerminalMode
          ? `<span class="session-time">${esc(displayTime)}</span>`
          : isRunning
          ? `<span class="session-status-dot running" title="运行中"></span>`
          : isPending
          ? `<span class="session-status-dot pending" title="${item.status === 'scheduled' ? '已定时' : '排队中'}"></span>`
          : `<span class="session-time">${esc(displayTime)}</span>`;
        return `
      <div class="chat-session-item ${isActive ? 'active' : ''}" data-item-id="${esc(item.id)}" onclick="selectConversation('${esc(item.id)}')">
        ${modeIcon}<span class="session-name" title="${esc(item.name)}">${esc(item.name)}</span>
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

  // ── 临时任务分组：ephemeral 会话 + 独立 ephemeral 任务 ──
  const ephemeralConvs  = convs.filter(c => c.ephemeral);
  const ephemeralTasks  = tasks.filter(t => t.ephemeral && !t.conversation_id);
  if (ephemeralConvs.length || ephemeralTasks.length) {
    const EPHEMERAL_KEY  = '__ephemeral__';
    const isCollapsed    = chatCollapsedProjectNames.has(EPHEMERAL_KEY);
    const encodedEphKey  = encodeURIComponent(EPHEMERAL_KEY).replace(/'/g, '%27');
    const ephItems = [];
    ephemeralConvs.forEach(c => {
      const convTasks = tasks.filter(t => t.conversation_id === c.id)
        .sort((a, b) => new Date(b.created || 0) - new Date(a.created || 0));
      const latestStatus = convTasks[0]?.status || 'done';
      ephItems.push({ type: 'conversation', id: c.id, name: c.name || c.id, time: c.updated || c.created || 0, status: latestStatus, mode: c.mode || 'chat' });
    });
    ephemeralTasks.forEach(t => {
      ephItems.push({ type: 'one-off', id: `task-${t.id}`, name: t.prompt, time: t.created || 0, status: t.status || 'done' });
    });
    ephItems.sort((a, b) => new Date(b.time) - new Date(a.time));

    let ephItemsHtml = '';
    if (!isCollapsed) {
      if (!ephItems.length) {
        ephItemsHtml = `<div class="chat-project-empty">暂无临时任务</div>`;
      } else {
        ephItems.forEach(item => {
          const isActive = item.id === activeConversationId || item.id === (activeConversationId ? 'task-' + activeConversationId : '');
          const dotClass = item.status === 'running' ? 'running' : item.status === 'pending' ? 'pending' : item.status === 'failed' ? 'failed' : 'done';
          ephItemsHtml += `<div class="chat-session-item ${isActive ? 'active' : ''}" data-item-id="${esc(item.id)}" onclick="selectConversation('${esc(item.id)}')">
            <span class="chat-session-dot ${dotClass}"></span>
            <span class="chat-session-name">${esc(item.name)}</span>
            <span class="chat-session-time">${fmtTime(item.time)}</span>
          </div>`;
        });
      }
    }

    html += `
  <div class="chat-project-group ${isCollapsed ? 'collapsed' : ''}">
    <div class="chat-project-header" onclick="toggleChatProjectGroup('${encodedEphKey}')" title="${isCollapsed ? '展开' : '收起'}临时任务">
      <div class="proj-header-title">
        <span class="proj-collapse-icon">${isCollapsed ? '+' : '-'}</span>
        <span style="margin-right:4px;opacity:.8">⚡</span>
        <span>临时任务</span>
      </div>
      <div class="proj-header-actions">
        <button class="proj-new-chat-btn" onclick="event.stopPropagation(); openEphemeralModal()" title="新建临时任务" aria-label="新建临时任务">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg>
        </button>
      </div>
    </div>
    <div class="chat-project-items">${ephItemsHtml}</div>
  </div>`;
  }

  list.innerHTML = html;
}

function rerenderChatProjectList() {
  if (chatSearchQuery.trim()) {
    renderChatSearchResults(chatSearchResults, chatSearchQuery.trim(), chatSearchDeep);
    return;
  }
  renderConversations(
    chatConversationsList,
    projectsCache,
    tasksCache,
  );
}

function chatSearchTypeLabel(type) {
  return {
    project: '项目',
    conversation: '对话',
    task: '任务',
    content: '内容',
  }[type] || type;
}

function chatSearchTargetId(result) {
  if (result.type === 'project') return result.project_name || result.id;
  if (result.type === 'conversation') return result.conversation_id || result.id;
  if (result.type === 'task' || result.type === 'content') {
    return result.conversation_id || (result.task_id ? `task-${result.task_id}` : result.id);
  }
  return result.id;
}

function renderChatSearchResults(results, query, deep = false, error = '') {
  const list = document.getElementById('chat-history-list');
  const q = String(query || '').trim();
  if (!q) {
    rerenderChatProjectList();
    return;
  }

  if (chatSearchLoading) {
    list.innerHTML = `<div class="chat-search-state">搜索中...</div>`;
    return;
  }

  if (error) {
    list.innerHTML = `<div class="chat-search-state error">搜索失败：${esc(error)}</div>`;
    return;
  }

  const header = `
    <div class="chat-search-summary">
      <span>${results.length ? `${results.length} 个结果` : '无匹配结果'}</span>
      ${deep ? '<span>已搜索内容</span>' : '<button type="button" onclick="runChatDeepSearch()">搜索内容</button>'}
    </div>`;

  if (!results.length) {
    list.innerHTML = `${header}<div class="chat-search-state">没有找到「${esc(q)}」</div>`;
    return;
  }

  const rows = results.map(result => {
    const target = encodeURIComponent(chatSearchTargetId(result));
    const projectName = encodeURIComponent(result.project_name || '');
    const match = (result.matches || [])[0];
    const snippet = match ? `<div class="chat-search-match">${esc(match.field)}：${esc(match.snippet)}</div>` : '';
    const status = result.status ? `<span class="chat-search-status">${esc(result.status)}</span>` : '';
    return `
      <div class="chat-search-result" onclick="openChatSearchResult('${esc(result.type)}', '${target}', '${projectName}')">
        <div class="chat-search-result-main">
          <div class="chat-search-title-row">
            <span class="chat-search-type">${esc(chatSearchTypeLabel(result.type))}</span>
            <span class="chat-search-title">${esc(result.title || result.id)}</span>
            ${status}
          </div>
          <div class="chat-search-subtitle">${esc(result.subtitle || '')}</div>
          ${snippet}
        </div>
      </div>`;
  }).join('');

  list.innerHTML = `${header}${rows}`;
}

async function performChatSearch(query, deep = false) {
  const q = String(query || '').trim();
  if (!q) return;
  chatSearchLoading = true;
  chatSearchDeep = deep;
  renderChatSearchResults(chatSearchResults, q, deep);
  try {
    const params = new URLSearchParams({ q, scope: 'all', limit: '80' });
    if (deep) params.set('deep', 'true');
    const r = await fetch(`${API}/api/search?${params.toString()}`);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || '搜索请求失败');
    if (q !== chatSearchQuery.trim()) return;
    chatSearchResults = data.results || [];
    chatSearchLoading = false;
    renderChatSearchResults(chatSearchResults, q, deep);
  } catch (e) {
    if (q !== chatSearchQuery.trim()) return;
    chatSearchLoading = false;
    renderChatSearchResults([], q, deep, e.message || String(e));
  }
}

function runChatDeepSearch() {
  const q = chatSearchQuery.trim();
  if (q) performChatSearch(q, true);
}

function clearChatSearchInput() {
  chatSearchQuery = '';
  chatSearchResults = [];
  chatSearchLoading = false;
  chatSearchDeep = false;
  const input = document.getElementById('chat-search-input');
  if (input) input.value = '';
}

function openChatSearchResult(type, encodedTarget, encodedProjectName) {
  const target = decodeURIComponent(encodedTarget || '');
  const projectName = decodeURIComponent(encodedProjectName || '');
  clearChatSearchInput();

  if (type === 'project') {
    if (projectName) {
      chatCollapsedProjectNames.delete(projectName);
      saveChatCollapsedState();
    }
    rerenderChatProjectList();
    return;
  }

  rerenderChatProjectList();
  if (target) selectConversation(target);
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
  chatSearchResults = [];
  chatSearchDeep = false;
  clearTimeout(chatSearchTimer);
  const q = chatSearchQuery.trim();
  if (!q) {
    chatSearchLoading = false;
    rerenderChatProjectList();
    return;
  }
  chatSearchLoading = true;
  renderChatSearchResults([], q);
  chatSearchTimer = setTimeout(() => performChatSearch(q), 220);
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
  // Capture the exact element that was clicked so rename targets the right DOM node,
  // not the duplicate in the "最近访问" section which querySelector would find first.
  const clickedItem = event.currentTarget.closest('.chat-session-item');
  const items = [];
  if (itemType === 'conversation') {
    items.push({ label: '重命名', icon: _menuIcons.rename, action: () => inlineRenameConversation(itemId, clickedItem) });
    items.push({ sep: true });
    items.push({ label: '归档', icon: _menuIcons.archive, action: () => archiveConversation(itemId) });
    items.push({ label: '删除', icon: _menuIcons.trash, danger: true, action: () => deleteConversation(itemId) });
  } else {
    items.push({ label: '归档', icon: _menuIcons.archive, action: () => archiveOneOff(itemId) });
    items.push({ label: '删除', icon: _menuIcons.trash, danger: true, action: () => deleteOneOff(itemId) });
  }
  openCtxMenu(event.currentTarget, items);
}

async function inlineRenameConversation(convId, itemEl) {
  if (!itemEl) {
    itemEl = document.querySelector(`.chat-session-item[data-item-id="${CSS.escape(convId)}"]`);
  }
  if (!itemEl) return;
  const nameSpan = itemEl.querySelector('.session-name');
  if (!nameSpan) return;

  const oldName = nameSpan.textContent;
  const input = document.createElement('input');
  input.type = 'text';
  input.value = oldName;
  input.className = 'session-rename-input';
  input.addEventListener('click', e => e.stopPropagation());
  nameSpan.replaceWith(input);
  input.focus();
  input.select();
  _isRenamingConversation = true;

  const commit = async () => {
    _isRenamingConversation = false;
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
    if (e.key === 'Escape') { _isRenamingConversation = false; rerenderChatProjectList(); }
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
  try { sessionStorage.setItem('coderfleet.activeConvId', convId || ''); } catch (_) {}
  activeTabId = upsertConversationTab(convId);
  renderTopbarTabs();
  rerenderChatProjectList();

  // 一次性任务 / 缓存里还没有这个会话（比如刚创建）：退回全量刷新一次。
  const conv = !convId.startsWith('task-') && chatConversationsList.find(c => c.id === convId);
  if (!conv) {
    await loadConversations();
    return;
  }

  try {
    const scopedTasks = await fetch(`${API}/api/tasks?conversation_id=${encodeURIComponent(convId)}&limit=200`).then(r => r.json());
    const byId = new Map(tasksCache.map(t => [t.id, t]));
    scopedTasks.forEach(t => byId.set(t.id, t));
    tasksCache = Array.from(byId.values());
  } catch (_) {}

  await renderChatWorkspace(conv);
  syncBottomTerminalProjectFromContext();
}

// 开启新会话
function startNewChat(options = {}) {
  stopChatFollow();
  activeConversationId = null;
  try { sessionStorage.removeItem('coderfleet.activeConvId'); } catch (_) {}
  currentChatTaskId = null;
  activeTabId = 'chat';
  chatNewSessionProject = options.projectName || '';
  showPage('chat');
  // showPage('chat') 已经会在后台触发一次 loadConversations()（见 nav.js 的 refreshCurrent），
  // 这里不再重复调用一次——之前这里会让每次点“+”都并发打两遍 /api/conversations、
  // /api/projects、/api/tasks?limit=1000。空状态本身只需要已缓存的项目信息，
  // 不用等那次全量刷新的网络请求跑完，直接同步渲染即可。
  renderEmptyChatState();
  syncBottomTerminalProjectFromContext();
}

// 渲染新会话空状态（无历史记录的初始界面）
async function renderEmptyChatState() {
  const workspace = document.getElementById('chat-workspace');
  if (!workspace) return;

  // Show chat workspace, hide terminal workspaces
  workspace.style.display = '';
  const termWs = document.getElementById('terminal-workspaces');
  if (termWs) termWs.style.display = 'none';

  const savedInput = document.getElementById('chat-input')?.value || '';

  const projectLabel = chatNewSessionProject || '未指定项目';
  currentChatProjectName = chatNewSessionProject;
  pendingImages = [];

  const newChatAccountName = projectsCache.find(p => p.name === chatNewSessionProject)?.account || '';
  const newChatAccountType = await getChatAccountType(newChatAccountName);
  const newChatModelPillHtml = newChatAccountType === 'claude' ? buildChatModelPillHtml() : '';

  workspace.innerHTML = `
<div class="chat-main-header">
  <div class="chat-main-title-area">
    <div class="chat-main-title">新对话</div>
    <div class="chat-main-subtitle">项目: <strong style="color: var(--accent);">${esc(projectLabel)}</strong>${newChatModelPillHtml}</div>
  </div>
  <div style="display:flex;gap:8px;align-items:center">
    <button id="files-panel-toggle-btn" class="btn files-panel-toggle-btn" onclick="toggleFilePanel()" title="浏览工作区文件">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6.5A2.5 2.5 0 015.5 4H10l2 2h6.5A2.5 2.5 0 0121 8.5v8A2.5 2.5 0 0118.5 19h-13A2.5 2.5 0 013 16.5z"/></svg>
      文件
    </button>
  </div>
</div>

<div class="chat-content-row">
  <div class="chat-messages-column">
    <div class="chat-viewport-wrap">
      <div class="chat-main-viewport" id="chat-viewport">
        <div id="chat-content">
          <div class="empty" style="margin-top: 60px;">输入第一条指令，开始与 AI 结对开发<br><span style="font-size:12px;color:var(--text-3)">连续对话会以任务链形式共享上下文；需要并行/条件编排请用「工作流」</span></div>
        </div>
      </div>
      <button id="scroll-to-bottom-btn" class="scroll-to-bottom-btn" onclick="scrollToBottomAndResume()" title="跳到底部，继续跟随输出">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        跳到底部
      </button>
    </div>
    ${buildChatInputHTML('输入您的开发指令... (按 Enter 发送，Shift+Enter 换行)', '发送第一条消息后将自动创建会话链')}
  </div>
  <div class="files-resize-handle" id="files-resize-handle" style="display:none"></div>
  <div class="chat-files-panel" id="chat-files-panel" style="display:none">
    <div class="files-panel-header">
      <span>工作区文件</span>
      <div style="display:flex;gap:4px;align-items:center">
        <button class="files-panel-icon-btn" onclick="loadFilePanelDir(_filePanelPath)" title="刷新">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/></svg>
        </button>
        <button class="files-panel-icon-btn" onclick="closeFilePanel()" title="关闭">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
        </button>
      </div>
    </div>
    <div class="files-panel-breadcrumb" id="files-panel-breadcrumb"></div>
    <div class="files-panel-tree" id="files-panel-tree">
      <div class="files-panel-empty">加载中...</div>
    </div>
  </div>
</div>
  `;

  const textarea = document.getElementById('chat-input');
  bindChatTextareaEvents(textarea);
  bindChatViewportScroll();
  if (savedInput && textarea) {
    textarea.value = savedInput;
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
  } else if (textarea) {
    textarea.focus();
  }
}

// ── @ 文件提及自动补全 ────────────────────────────────────
const CHAT_MENTION_NAV_KEYS = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'];

function _detectChatMentionToken(textarea) {
  const value = textarea.value;
  const cursor = textarea.selectionStart;
  if (cursor !== textarea.selectionEnd) return null;
  let i = cursor - 1;
  while (i >= 0 && !/\s/.test(value[i]) && value[i] !== '@') i--;
  if (i < 0 || value[i] !== '@') return null;
  if (i > 0 && !/\s/.test(value[i - 1])) return null;
  return { start: i, query: value.slice(i + 1, cursor) };
}

function _handleChatMentionInput(textarea) {
  if (!currentChatProjectName) {
    _closeChatMentionDropdown();
    return;
  }
  const token = _detectChatMentionToken(textarea);
  if (!token) {
    _closeChatMentionDropdown();
    return;
  }
  chatMentionOpen = true;
  chatMentionStart = token.start;
  chatMentionQuery = token.query;
  chatMentionActiveIndex = 0;
  clearTimeout(chatMentionTimer);
  chatMentionTimer = setTimeout(() => _performChatMentionSearch(token.query), 150);
}

async function _performChatMentionSearch(query) {
  const project = currentChatProjectName;
  if (!project || !chatMentionOpen) return;
  // 用递增序号而非 query 字符串判断新旧：两次请求 query 相同（比如输入又退格回同样的文本）
  // 时，字符串比较无法分辨谁更新，序号比较可以。
  const requestSeq = ++chatMentionRequestSeq;
  try {
    const params = new URLSearchParams({ q: query, limit: '30' });
    const r = await fetch(`${API}/api/projects/${encodeURIComponent(project)}/files/search?${params.toString()}`);
    const data = await r.json().catch(() => []);
    if (!r.ok) throw new Error((data && data.detail) || '搜索失败');
    if (requestSeq !== chatMentionRequestSeq || !chatMentionOpen || currentChatProjectName !== project) return;
    chatMentionResults = Array.isArray(data) ? data : [];
    chatMentionActiveIndex = 0;
    _renderChatMentionDropdown();
  } catch (e) {
    if (requestSeq !== chatMentionRequestSeq || !chatMentionOpen) return;
    chatMentionResults = [];
    _renderChatMentionDropdown();
  }
}

function _renderChatMentionDropdown() {
  const el = document.getElementById('chat-mention-dropdown');
  if (!el) return;
  if (!chatMentionOpen) {
    el.style.display = 'none';
    el.innerHTML = '';
    return;
  }
  el.style.display = 'block';
  if (!chatMentionResults.length) {
    el.innerHTML = `<div class="chat-mention-empty">没有匹配的文件</div>`;
    return;
  }
  el.innerHTML = chatMentionResults.map((entry, idx) => {
    const icon = entry.is_dir ? _folderIcon() : _fileIcon(entry.name);
    const active = idx === chatMentionActiveIndex ? ' active' : '';
    return `<div class="chat-mention-item${active}" onmousedown="event.preventDefault(); _confirmChatMentionSelection(${idx})">
      ${icon}<span class="chat-mention-path">${esc(entry.path)}</span>
    </div>`;
  }).join('');
  _scrollActiveDropdownItemIntoView(el);
}

function _confirmChatMentionSelection(idx) {
  const entry = chatMentionResults[idx];
  const textarea = document.getElementById('chat-input');
  if (!entry || !textarea) {
    _closeChatMentionDropdown();
    return;
  }
  const value = textarea.value;
  const cursor = textarea.selectionStart;
  const before = value.slice(0, chatMentionStart);
  const after = value.slice(cursor);
  const insertion = `@${entry.path} `;
  textarea.value = before + insertion + after;
  const newCursor = before.length + insertion.length;
  textarea.selectionStart = textarea.selectionEnd = newCursor;
  _closeChatMentionDropdown();
  textarea.focus();
  textarea.dispatchEvent(new Event('input'));
}

function _closeChatMentionDropdown() {
  chatMentionOpen = false;
  chatMentionQuery = '';
  chatMentionStart = -1;
  chatMentionResults = [];
  chatMentionActiveIndex = 0;
  clearTimeout(chatMentionTimer);
  _renderChatMentionDropdown();
}

function _validateChatMentionCursor(textarea) {
  if (!chatMentionOpen) return;
  const token = _detectChatMentionToken(textarea);
  if (!token || token.start !== chatMentionStart) {
    _closeChatMentionDropdown();
  }
}

let _chatMentionOutsideClickBound = false;
function _initChatMentionOutsideClick() {
  if (_chatMentionOutsideClickBound) return;
  _chatMentionOutsideClickBound = true;
  document.addEventListener('mousedown', e => {
    if (!chatMentionOpen) return;
    const dropdown = document.getElementById('chat-mention-dropdown');
    const textarea = document.getElementById('chat-input');
    if (dropdown && dropdown.contains(e.target)) return;
    if (textarea && e.target === textarea) return;
    _closeChatMentionDropdown();
  });
}

// ── / Skill 命令自动补全 ──────────────────────────────────
// 只在输入框最开头触发（与后端 Scheduler._expand_skill_command 的匹配规则一致，
// 后端只把"整段 prompt 以 /slug 开头"这种情况当作命令展开，其余一律原样透传）。
function _detectChatSlashToken(textarea) {
  const value = textarea.value;
  const cursor = textarea.selectionStart;
  if (cursor !== textarea.selectionEnd) return null;
  if (value[0] !== '/') return null;
  const m = /^\/(\S*)/.exec(value);
  if (!m) return null;
  const tokenEnd = 1 + m[1].length;
  if (cursor > tokenEnd) return null; // 命令词已经打完（后面出现空格/参数），收起下拉
  return { start: 0, query: value.slice(1, cursor) };
}

function _currentChatAccountName() {
  const proj = projectsCache.find(p => p.name === currentChatProjectName);
  return proj ? proj.account : '';
}

async function _loadChatSlashSkills(account) {
  if (chatSlashSkillsCache.account === account) return chatSlashSkillsCache.skills;
  try {
    const r = await fetch(`${API}/api/accounts/${encodeURIComponent(account)}/skills`);
    const data = await r.json().catch(() => []);
    const skills = (r.ok && Array.isArray(data)) ? data.filter(s => s.user_invocable !== false) : [];
    chatSlashSkillsCache = { account, skills };
    return skills;
  } catch (e) {
    return [];
  }
}

async function _handleChatSlashInput(textarea) {
  const account = _currentChatAccountName();
  const token = _detectChatSlashToken(textarea);
  if (!account || !token) {
    _closeChatSlashDropdown();
    return;
  }
  // 同一时刻只能有一个下拉框，@ 提及优先级更低
  _closeChatMentionDropdown();
  chatSlashOpen = true;
  chatSlashStart = token.start;
  chatSlashActiveIndex = 0;
  const requestSeq = ++chatSlashRequestSeq;
  const skills = await _loadChatSlashSkills(account);
  if (requestSeq !== chatSlashRequestSeq || !chatSlashOpen) return;
  const q = token.query.toLowerCase();
  chatSlashResults = skills.filter(s =>
    s.slug.toLowerCase().includes(q) || (s.name || '').toLowerCase().includes(q)
  );
  chatSlashActiveIndex = 0;
  _renderChatSlashDropdown();
}

// 键盘上下选中后，把 active 项滚动到可视区域内（下拉框本身带 overflow-y: auto）
function _scrollActiveDropdownItemIntoView(el) {
  const active = el.querySelector('.chat-mention-item.active');
  if (active) active.scrollIntoView({ block: 'nearest' });
}

function _renderChatSlashDropdown() {
  const el = document.getElementById('chat-slash-dropdown');
  if (!el) return;
  if (!chatSlashOpen) {
    el.style.display = 'none';
    el.innerHTML = '';
    return;
  }
  el.style.display = 'block';
  if (!chatSlashResults.length) {
    el.innerHTML = `<div class="chat-mention-empty">没有匹配的技能</div>`;
    return;
  }
  el.innerHTML = chatSlashResults.map((entry, idx) => {
    const active = idx === chatSlashActiveIndex ? ' active' : '';
    return `<div class="chat-mention-item${active}" onmousedown="event.preventDefault(); _confirmChatSlashSelection(${idx})">
      <span class="chat-mention-path">/${esc(entry.slug)}</span>${entry.description ? `<span class="chat-slash-desc">${esc(entry.description)}</span>` : ''}
    </div>`;
  }).join('');
  _scrollActiveDropdownItemIntoView(el);
}

function _confirmChatSlashSelection(idx) {
  const entry = chatSlashResults[idx];
  const textarea = document.getElementById('chat-input');
  if (!entry || !textarea) {
    _closeChatSlashDropdown();
    return;
  }
  const value = textarea.value;
  const cursor = textarea.selectionStart;
  const after = value.slice(cursor);
  const insertion = `/${entry.slug} `;
  textarea.value = insertion + after;
  const newCursor = insertion.length;
  textarea.selectionStart = textarea.selectionEnd = newCursor;
  _closeChatSlashDropdown();
  textarea.focus();
  textarea.dispatchEvent(new Event('input'));
}

function _closeChatSlashDropdown() {
  chatSlashOpen = false;
  chatSlashStart = -1;
  chatSlashResults = [];
  chatSlashActiveIndex = 0;
  _renderChatSlashDropdown();
}

function _validateChatSlashCursor(textarea) {
  if (!chatSlashOpen) return;
  const token = _detectChatSlashToken(textarea);
  if (!token) _closeChatSlashDropdown();
}

let _chatSlashOutsideClickBound = false;
function _initChatSlashOutsideClick() {
  if (_chatSlashOutsideClickBound) return;
  _chatSlashOutsideClickBound = true;
  document.addEventListener('mousedown', e => {
    if (!chatSlashOpen) return;
    const dropdown = document.getElementById('chat-slash-dropdown');
    const textarea = document.getElementById('chat-input');
    if (dropdown && dropdown.contains(e.target)) return;
    if (textarea && e.target === textarea) return;
    _closeChatSlashDropdown();
  });
}

// 自适应高度及快捷键发送绑定
function bindChatTextareaEvents(textarea) {
  if (!textarea) return;
  _initChatMentionOutsideClick();
  _initChatSlashOutsideClick();
  textarea.addEventListener('input', () => {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
    _handleChatSlashInput(textarea);
    _handleChatMentionInput(textarea);
    // 任务运行中时，输入框内容的有无决定发送/停止合并按钮的外观
    if (currentChatTaskId) _syncChatSendButton(currentChatTaskId, _isChatQueueFull(activeConversationId));
  });
  textarea.addEventListener('click', () => {
    _validateChatSlashCursor(textarea);
    _validateChatMentionCursor(textarea);
  });
  textarea.addEventListener('keyup', e => {
    if (CHAT_MENTION_NAV_KEYS.includes(e.key)) {
      _validateChatSlashCursor(textarea);
      _validateChatMentionCursor(textarea);
    }
  });
  textarea.addEventListener('keydown', e => {
    if (chatSlashOpen) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (chatSlashResults.length) {
          const dir = e.key === 'ArrowDown' ? 1 : -1;
          chatSlashActiveIndex = (chatSlashActiveIndex + dir + chatSlashResults.length) % chatSlashResults.length;
          _renderChatSlashDropdown();
        }
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        if (e.isComposing || e.keyCode === 229) return;
        e.preventDefault();
        if (chatSlashResults.length) {
          _confirmChatSlashSelection(chatSlashActiveIndex);
        } else {
          _closeChatSlashDropdown();
        }
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        _closeChatSlashDropdown();
        return;
      }
    } else if (chatMentionOpen) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (chatMentionResults.length) {
          const dir = e.key === 'ArrowDown' ? 1 : -1;
          chatMentionActiveIndex = (chatMentionActiveIndex + dir + chatMentionResults.length) % chatMentionResults.length;
          _renderChatMentionDropdown();
        }
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        if (e.isComposing || e.keyCode === 229) return;
        e.preventDefault();
        if (chatMentionResults.length) {
          _confirmChatMentionSelection(chatMentionActiveIndex);
        } else {
          _closeChatMentionDropdown();
        }
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        _closeChatMentionDropdown();
        return;
      }
    }
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

function scrollChatViewportToBottom(force = false) {
  if (!force && chatUserScrolledUp) return;
  const viewport = document.getElementById('chat-viewport');
  if (viewport) {
    viewport.scrollTop = viewport.scrollHeight;
  }
}

function _updateScrollToBottomBtn(show) {
  const btn = document.getElementById('scroll-to-bottom-btn');
  if (btn) btn.style.display = show ? 'flex' : 'none';
}

function scrollToBottomAndResume() {
  chatUserScrolledUp = false;
  _updateScrollToBottomBtn(false);
  const viewport = document.getElementById('chat-viewport');
  if (viewport) viewport.scrollTop = viewport.scrollHeight;
}

function bindChatViewportScroll() {
  chatUserScrolledUp = false;
  _updateScrollToBottomBtn(false);
  const viewport = document.getElementById('chat-viewport');
  if (!viewport) return;
  viewport.addEventListener('scroll', function () {
    const atBottom = viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight - 80;
    chatUserScrolledUp = !atBottom;
    _updateScrollToBottomBtn(chatUserScrolledUp);
  }, { passive: true });
}

// 新建终端对话
async function startNewTerminalConversation(projectName) {
  if (!projectName) return;
  const now = new Date();
  const name = `Terminal ${String(now.getMonth()+1).padStart(2,'0')}/${String(now.getDate()).padStart(2,'0')} ${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;
  try {
    const r = await fetch(`${API}/api/conversations/terminal`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, project_name: projectName }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      alert(`创建终端会话失败：${err.detail || r.status}`);
      return;
    }
    const conv = await r.json();
    await loadConversations(false);
    activeConversationId = conv.id;
    activeTabId = upsertConversationTab(conv.id);
    renderTopbarTabs();
    await loadConversations(true);
  } catch (e) {
    alert(`创建终端会话失败：${e.message}`);
  }
}

// 渲染终端对话工作区
function renderTerminalConversationWorkspace(conv) {
  currentChatProjectName = conv.project_name || conv.project?.split('/').pop() || '';

  // Persistent container: hide chat workspace, show terminal-workspaces panel.
  // Terminal conversation DOMs live here and are NEVER destroyed on tab switches —
  // only show/hidden — so the xterm instance and WebSocket stay alive.
  const chatWs  = document.getElementById('chat-workspace');
  const termWs  = document.getElementById('terminal-workspaces');
  if (!termWs) return;
  if (chatWs)  chatWs.style.display  = 'none';
  termWs.style.display = 'flex';

  // Hide all other terminal conv workspaces (only one visible at a time)
  termWs.querySelectorAll('.terminal-conv-workspace').forEach(el => { el.style.display = 'none'; });

  // Check if this conversation's container already exists
  let convWs = document.getElementById(`terminal-conv-ws-${conv.id}`);
  if (convWs) {
    convWs.style.display = 'flex';
    const ctx = convTerminalContexts[conv.id];
    if (ctx && ctx.socket &&
        (ctx.socket.readyState === WebSocket.OPEN || ctx.socket.readyState === WebSocket.CONNECTING)) {
      // Still connected — just resize/focus, no reconnect needed
      resizeConvTerminal(ctx);
      ctx.terminal?.focus();
      return;
    }
    // Container exists but socket died — reconnect
    const mount = document.getElementById(`conv-terminal-container-${conv.id}`);
    if (mount) connectConversationTerminal(conv.id, mount);
    return;
  }

  // First time: create persistent workspace for this terminal conversation
  convWs = document.createElement('div');
  convWs.id        = `terminal-conv-ws-${conv.id}`;
  convWs.className = 'terminal-conv-workspace';
  convWs.style.cssText = 'display:flex;flex-direction:column;flex:1;min-height:0;width:100%;';
  convWs.innerHTML = `
<div class="chat-main-header">
  <div class="chat-main-title-area">
    <div class="chat-main-title" title="${esc(conv.name)}">
      <span style="margin-right:6px;opacity:.7">⌨</span>${esc(conv.name)}
    </div>
    <div class="chat-main-subtitle">
      项目: <strong style="color:var(--accent);">${esc(conv.project_name || conv.project?.split('/').pop() || '未知')}</strong> ·
      账号: <span>${esc(conv.account || '未指定')}</span> ·
      活跃: <span>${fmtTime(conv.updated)}</span>
    </div>
  </div>
  <div style="display:flex;gap:8px;align-items:center;" id="chat-header-actions">
    <span class="status-dot killed" id="conv-terminal-dot-${esc(conv.id)}" title="未连接" style="width:8px;height:8px;"></span>
    <span id="conv-terminal-status-${esc(conv.id)}" style="font-size:11px;color:var(--text-3);">连接中...</span>
    <button class="btn" style="font-size:11px;padding:3px 8px;" onclick="reconnectConversationTerminal(${JSON.stringify(conv.id)})" title="重新连接">重连</button>
  </div>
</div>
<div style="flex:1;min-height:0;overflow:hidden;background:#1a1b1e;" id="conv-terminal-container-${esc(conv.id)}"></div>
`;
  termWs.appendChild(convWs);

  requestAnimationFrame(() => {
    const mount = document.getElementById(`conv-terminal-container-${conv.id}`);
    if (mount) connectConversationTerminal(conv.id, mount);
  });
}

function reconnectConversationTerminal(convId) {
  const ctx = convTerminalContexts[convId];
  if (ctx) {
    ctx.terminal?.dispose();
    ctx.terminal = null;
    ctx.fitAddon = null;
    disconnectConversationTerminal(convId, false);
  }
  const mount = document.getElementById(`conv-terminal-container-${convId}`);
  if (mount) connectConversationTerminal(convId, mount);
}

// 渲染右侧会话主工作区
async function renderChatWorkspace(conv) {
  const workspace = document.getElementById('chat-workspace');
  if (!workspace) return;

  // Terminal-mode conversations get a full-height xterm, not chat bubbles
  if (conv.mode === 'terminal') {
    return renderTerminalConversationWorkspace(conv);
  }

  // Show chat workspace, hide persistent terminal workspaces panel
  workspace.style.display = '';
  const termWs = document.getElementById('terminal-workspaces');
  if (termWs) termWs.style.display = 'none';

  const savedInput = document.getElementById('chat-input')?.value || '';

  currentChatProjectName = conv.project_name || conv.project?.split('/').pop() || '';
  pendingImages = [];

  const chatAccount = await getChatAccount(conv.account);
  const chatAccountType = chatAccount?.type || '';
  const modelPillHtml = chatAccountType === 'claude' ? buildChatModelPillHtml() : '';
  const usagePillHtml = buildChatUsagePillHtml(chatAccount);

  workspace.innerHTML = `
<!-- 会话头部 -->
<div class="chat-main-header">
  <div class="chat-main-title-area">
    <div class="chat-main-title" title="${esc(conv.name)}">${esc(conv.name)}${conv.ephemeral ? ' <span style="font-size:11px;color:var(--accent);font-weight:normal" title="临时容器会话，每条消息在独立容器中运行">⚡ 临时</span>' : ''}</div>
    <div class="chat-main-subtitle">
      项目: <strong style="color: var(--accent);">${esc(conv.project_name || conv.project?.split('/').pop() || '未知')}</strong> ·
      账号: <span>${esc(conv.account || '未指定')}</span> ·
      活跃: <span>${fmtTime(conv.updated)}</span>${modelPillHtml}${usagePillHtml}
    </div>
  </div>
  <div style="display: flex; gap: 8px; align-items: center;">
    <button id="files-panel-toggle-btn" class="btn files-panel-toggle-btn" onclick="toggleFilePanel()" title="浏览工作区文件">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6.5A2.5 2.5 0 015.5 4H10l2 2h6.5A2.5 2.5 0 0121 8.5v8A2.5 2.5 0 0118.5 19h-13A2.5 2.5 0 013 16.5z"/></svg>
      文件
    </button>
  </div>
</div>

<!-- 中间区域（横向布局） -->
<div class="chat-content-row">
  <!-- 消息列（消息流 + 输入框） -->
  <div class="chat-messages-column">
    <div class="chat-viewport-wrap">
      <div class="chat-main-viewport" id="chat-viewport">
        <div id="chat-content"></div>
      </div>
      <button id="scroll-to-bottom-btn" class="scroll-to-bottom-btn" onclick="scrollToBottomAndResume()" title="跳到底部，继续跟随输出">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        跳到底部
      </button>
    </div>
    ${buildChatInputHTML('输入您下一轮的指令... (按 Enter 发送，Shift+Enter 换行)', conv.isOneOff ? '一次性单任务 (发送消息升级为任务链)' : 'AI 连续问答 · 会话链模式')}
  </div>
  <!-- 拖拽分隔线 -->
  <div class="files-resize-handle" id="files-resize-handle" style="display:none"></div>
  <!-- 文件面板 -->
  <div class="chat-files-panel" id="chat-files-panel" style="display:none">
    <div class="files-panel-header">
      <span>工作区文件</span>
      <div style="display:flex;gap:4px;align-items:center">
        <button class="files-panel-icon-btn" onclick="loadFilePanelDir(_filePanelPath)" title="刷新">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/></svg>
        </button>
        <button class="files-panel-icon-btn" onclick="closeFilePanel()" title="关闭">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
        </button>
      </div>
    </div>
    <div class="files-panel-breadcrumb" id="files-panel-breadcrumb"></div>
    <div class="files-panel-tree" id="files-panel-tree">
      <div class="files-panel-empty">加载中...</div>
    </div>
  </div>
</div>
  `;

  restoreFilePanelIfOpen();

  const textarea = document.getElementById('chat-input');
  bindChatTextareaEvents(textarea);
  if (savedInput && textarea) {
    textarea.value = savedInput;
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
  }

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

    // 恢复本会话上次选中的模型（会话期内记忆），否则回退到最近一条任务实际使用的模型
    const modelSelect = document.getElementById('chat-model-select');
    if (modelSelect) {
      const lastModel = convTasks.length ? (convTasks[convTasks.length - 1].model || '') : '';
      const remembered = Object.prototype.hasOwnProperty.call(chatModelByConversation, conv.id)
        ? chatModelByConversation[conv.id]
        : lastModel;
      modelSelect.value = remembered;
      if (modelSelect.value !== remembered) modelSelect.value = ''; // 未知模型值时回退到默认选项
    }

    const runningTask = convTasks.find(t => t.status === 'running');
    currentChatTaskId = runningTask ? runningTask.id : null;

    const runningCount = convTasks.filter(t => t.status === 'running').length;
    const pendingCount = convTasks.filter(t => t.status === 'pending').length;
    const scheduledCount = convTasks.filter(t => t.status === 'scheduled').length;

    // 渲染排队面板（只含 pending/scheduled 且排好序的任务）
    const queuedTasks = _getQueuedChatTasks(conv.id);
    renderQueuePanel(queuedTasks);

    // 同步发送/停止合并按钮（队列已满时，仅在"发送"外观下禁用）
    _syncChatSendButton(currentChatTaskId, queuedTasks.length >= CHAT_MAX_QUEUE);

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

    // 只有 done/failed/killed/running 的任务才会渲染消息气泡（排队/定时中的任务跳过，由队列面板管理）
    const renderableTasks = convTasks.filter(t => t.status !== 'pending' && t.status !== 'scheduled');

    // 会话历史越长，日志越大，一次性把全部任务日志都拉下来渲染就越慢（issue #42）。
    // 只 eager 拉取渲染最近 CHAT_EAGER_LOG_COUNT 条，更早的任务先显示折叠占位，
    // 点开才按需拉取渲染，命中的话同样写入 _finishedLogCache 复用。
    const eagerStartIdx = Math.max(0, renderableTasks.length - CHAT_EAGER_LOG_COUNT);
    const collapsedTasks = renderableTasks.slice(0, eagerStartIdx);
    const eagerTasks = renderableTasks.slice(eagerStartIdx);

    // 之前手动点开过的历史任务：重渲染（比如心跳触发的整体重渲染）时保持展开状态，
    // 不要把用户已经展开的记录又折叠回去。
    const manuallyExpanded = collapsedTasks.filter(t => chatExpandedTaskIds.has(t.id));
    const expandedLogsById = new Map();

    // 并行获取最近这几条任务的日志，以及手动展开过的历史任务的日志；done/failed/killed
    // 任务的日志内容不会再变，命中缓存就直接复用，避免会话因为其它任务状态变化整体
    // 重渲染时全部重新拉取。
    const [logs, expandedLogs] = await Promise.all([
      Promise.all(eagerTasks.map(t => _fetchTaskLogCached(t))),
      Promise.all(manuallyExpanded.map(t => _fetchTaskLogCached(t))),
    ]);
    manuallyExpanded.forEach((t, idx) => expandedLogsById.set(t.id, expandedLogs[idx]));
    chatContent.innerHTML = '';

    collapsedTasks.forEach(task => {
      if (expandedLogsById.has(task.id)) {
        _renderChatTaskTurn(chatContent, task, expandedLogsById.get(task.id));
      } else {
        chatContent.appendChild(_renderCollapsedTaskPlaceholder(task));
      }
    });

    // 循环独立渲染最近几个任务的提问和日志
    let _lastLocalRenderer = null;
    eagerTasks.forEach((task, idx) => {
      _lastLocalRenderer = _renderChatTaskTurn(chatContent, task, logs[idx]);
      const logWrap = chatContent.lastElementChild;
      const isLastTask = idx === eagerTasks.length - 1;
      _selfHealStuckToolStatus(task, logWrap).then(healedRenderer => {
        if (healedRenderer && isLastTask) chatRenderer = healedRenderer;
      });
    });
    if (_lastLocalRenderer) chatRenderer = _lastLocalRenderer;

    scrollToBottomAndResume();
    bindChatViewportScroll();

    // 追踪任务选择：优先正在运行的任务，其次是第一个 pending 任务，最后是第一个 scheduled 任务
    const activeTask = runningTask
      || convTasks.find(t => t.status === 'pending')
      || convTasks.find(t => t.status === 'scheduled');
    if (activeTask) {
      // chatRenderer 始终对应最后一个任务的渲染器，而最后一个任务必然落在 eagerTasks 里。
      // 若 activeTask 就是最后一个任务（最常见情况），把其日志字节数告知服务端，
      // 服务端从该偏移量开始推送，避免重新打开会话时内容重复。
      const lastRenderable = renderableTasks[renderableTasks.length - 1];
      const alreadyRenderedLog = (lastRenderable && activeTask.id === lastRenderable.id)
        ? (logs[logs.length - 1] || '') : '';
      const skipBytes = new TextEncoder().encode(alreadyRenderedLog).byteLength;
      startChatFollow(activeTask.id, skipBytes);
    }
  } catch (e) {
    chatContent.innerHTML = `<div style="color:var(--red);padding:16px">加载会话历史失败: ${esc(e.message)}<br><pre style="font-size:11px;color:var(--text-3);margin-top:8px">${esc(e.stack)}</pre></div>`;
  }
}

// 打开会话时 eager 拉取渲染的最近任务条数，更早的任务折叠懒加载（issue #42）
const CHAT_EAGER_LOG_COUNT = 8;

// 判断一份任务日志是不是"真的收尾了"——跟 renderer.js render() 里 isFooterSeparator
// 用的是同一套约定：一行 "======" 之后（跳过空行）紧跟 finished: 或 usage status:。
// 这两行只有 scheduler.py 的 _append_usage_status/_append_log_footer 真正跑完才会
// 写进日志文件，而 task.status 翻成 done/failed/killed 发生在这之前（中间隔着一次
// 可能耗时的 docker exec）。用它当"这份日志是否可以放心长期缓存"的信号——宁可因为
// 判定过严多拉几次，也不要把一份还没收尾的半成品当成定论存进 IndexedDB 里出不来。
function _looksLikeCompleteTaskLog(text) {
  const lines = (text || '').split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (!lines[i].trim().startsWith('======')) continue;
    for (let j = i + 1; j < lines.length; j++) {
      const next = lines[j].trim();
      if (!next) continue;
      if (next.startsWith('finished:') || next.startsWith('usage status:')) return true;
      break;
    }
  }
  return false;
}

// 拉取单条任务日志；done/failed/killed 任务的日志内容不会再变，命中缓存直接复用。
// 缓存分两层：内存 Map（当前会话页面内最快）+ IndexedDB（跨页面刷新持久化，issue #43）。
async function _fetchTaskLogCached(task) {
  if (task.status !== 'running') {
    if (_finishedLogCache.has(task.id)) return _finishedLogCache.get(task.id);
    const persisted = await logCacheGet(task.id);
    if (persisted !== null) {
      _finishedLogCache.set(task.id, persisted);
      return persisted;
    }
  }
  const text = await fetch(`${API}/api/tasks/${task.id}/logs?light=1`).then(r => r.text()).catch(() => '');
  if (task.status !== 'running' && _looksLikeCompleteTaskLog(text)) {
    _finishedLogCache.set(task.id, text);
    logCacheSet(task.id, text);
  }
  return text;
}

// 自愈：任务已经确定结束（done/failed/killed），日志里却还有工具调用卡片停在 ⏳——
// 说明当初写进缓存的那份日志被抓取时还没真正收尾（见 _looksLikeCompleteTaskLog 的
// 注释），是这次修复之前就已经写进某个用户 IndexedDB 里的坏缓存，光靠"以后不再写
// 坏数据"救不回来。这里检测到这种卡死状态就清掉缓存、强制重新拉一次、重新渲染，
// 用户不用手动刷新好几轮或者清浏览器数据才能恢复。
async function _selfHealStuckToolStatus(task, logWrap) {
  if (task.status === 'running' || task.status === 'pending' || task.status === 'scheduled') return;
  const stuckPending = [...logWrap.querySelectorAll('.tool-status')]
    .some(el => el.textContent.trim() === '⏳');
  if (!stuckPending) return;

  _finishedLogCache.delete(task.id);
  await logCacheDelete(task.id).catch(() => {});
  const freshText = await fetch(`${API}/api/tasks/${task.id}/logs?light=1`).then(r => r.text()).catch(() => '');
  if (!freshText || !_looksLikeCompleteTaskLog(freshText)) return; // 服务端也还没收尾，下次再自愈

  _finishedLogCache.set(task.id, freshText);
  logCacheSet(task.id, freshText);

  logWrap.innerHTML = '';
  const healedRenderer = new ChatLogRenderer(logWrap, false, true, task.project_name || null, task.id);
  healedRenderer.render(freshText, task.type);
  return healedRenderer;
}

// 渲染单个任务的完整问答回合（用户气泡 + 日志渲染），返回对应的 ChatLogRenderer
function _renderChatTaskTurn(container, task, logText) {
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
      ${renderTaskFileAttachments(task, currentChatProjectName)}
    </div>`;
  container.appendChild(userWrap);

  // 2. 渲染日志输出的容器
  const logWrap = document.createElement('div');
  logWrap.style.marginBottom = '24px';
  container.appendChild(logWrap);

  // 3. 构建局部的 ChatLogRenderer 并进行渲染
  // 每一个任务在渲染时，均传入 foldProcess=true，把中间执行步骤折叠起来，把回复直接展现
  const localRenderer = new ChatLogRenderer(logWrap, task.status === 'running', true, task.project_name || null, task.id);
  localRenderer.render(logText, task.type);
  return localRenderer;
}

// 更早历史任务的折叠占位：只显示时间 + prompt 摘要，不请求日志正文；
// 点开才按需拉取渲染，原地替换掉占位节点。
function _renderCollapsedTaskPlaceholder(task) {
  const wrap = document.createElement('div');
  wrap.className = 'chat-collapsed-task';
  const promptPreview = (task.prompt || '').replace(/\s+/g, ' ').trim();
  wrap.innerHTML = `
    <span class="chat-collapsed-task-caret">▸</span>
    <span class="chat-collapsed-task-time">${esc(fmtTime(task.created))}</span>
    <span class="chat-collapsed-task-prompt">${esc(promptPreview)}</span>`;
  wrap.title = '点击展开完整记录';
  wrap.addEventListener('click', async () => {
    if (wrap.dataset.expanding) return;
    wrap.dataset.expanding = '1';
    wrap.style.opacity = '0.6';
    // 记录展开状态：会话因心跳等原因整体重渲染时，renderChatWorkspace 会读取这个
    // 集合，让这条历史任务直接以展开态渲染，而不是又变回折叠占位。
    chatExpandedTaskIds.add(task.id);
    const logText = await _fetchTaskLogCached(task);
    const expanded = document.createElement('div');
    _renderChatTaskTurn(expanded, task, logText);
    wrap.replaceWith(expanded);
  });
  return wrap;
}

// 发送前判断项目绑定账号是否已有任务在跑，决定占位态（排队 or 执行中）
function _isProjectAccountBusy(projectName) {
  const project = (projectsCache || []).find(p => p.name === projectName);
  if (!project) return false;
  return (tasksCache || []).some(t => t.account === project.account && t.status === 'running');
}

// 乐观 UI：无需整页重渲就地追加用户气泡 + 等待中响应区
// isPending: true → 任务进入排队，只加用户气泡 + 刷新队列面板，不加占位 bubble，不开 SSE
// isPending: false → 立即执行，加用户气泡 + 执行中占位 + 开 SSE
// 返回 true 表示操作成功（已找到 chat-content）
function _appendOptimisticUserMessage(promptText, snapshotPaths, taskId, isPending = false, accountType = '') {
  const chatContent = document.getElementById('chat-content');
  if (!chatContent) return false;

  // 排队中的任务不立即显示气泡，等真正执行时再展示；由下方队列面板管理
  if (!isPending) {
    const userWrap = document.createElement('div');
    userWrap.className = 'timeline-node-wrapper user-wrapper';
    userWrap.innerHTML = `
      <div class="user-bubble">
        <div class="user-bubble-title-row">
          <div class="user-bubble-title">你:</div>
          <button class="user-copy-btn" onclick="copyUserBubble(this)" title="复制">${copyBtnSVG()}</button>
        </div>
        <div class="user-bubble-content">${esc(promptText)}</div>
        ${renderTaskFileAttachments({ images: snapshotPaths, project_name: currentChatProjectName }, currentChatProjectName)}
      </div>`;
    chatContent.appendChild(userWrap);
  }

  currentChatTaskId = taskId;

  if (isPending) {
    // 排队中：不在聊天气泡区显示占位，改由输入框上方的队列面板管理
    const statusText = document.getElementById('chat-status-text');
    if (statusText) {
      const pendingCount = (tasksCache || []).filter(
        t => t.conversation_id === activeConversationId && (t.status === 'pending' || t.status === 'scheduled')
      ).length;
      const runningCount = (tasksCache || []).filter(
        t => t.conversation_id === activeConversationId && t.status === 'running'
      ).length;
      if (runningCount > 0) {
        statusText.innerHTML = `<span style="color: var(--green); animation: pulse 1.5s infinite;">● AI 正在执行任务...</span> <span style="color: var(--text-2); font-size: 11px;">(${pendingCount}个任务在排队...)</span>`;
      } else {
        statusText.innerHTML = `<span style="color: var(--text-2);">● 任务在排队中 (${pendingCount}个)</span>`;
      }
    }
    // 从 tasksCache 刷新队列面板（调用前调用方已把新任务 push 进 tasksCache）
    _refreshQueuePanel();
    scrollToBottomAndResume();
  } else {
    // 立即执行：加 "执行中" 占位，开 SSE
    const logWrap = document.createElement('div');
    logWrap.style.marginBottom = '24px';
    chatContent.appendChild(logWrap);

    const localRenderer = new ChatLogRenderer(logWrap, true, true, currentChatProjectName, taskId);
    localRenderer.accountType = accountType || (tasksCache || []).find(t => t.id === taskId)?.type || '';
    localRenderer.renderExecuting();
    chatRenderer = localRenderer;

    currentChatTaskId = taskId;
    _syncChatSendButton(taskId, false);
    const statusText = document.getElementById('chat-status-text');
    if (statusText) {
      statusText.innerHTML = `<span style="color: var(--green); animation: pulse 1.5s infinite;">● AI 正在执行任务...</span>`;
    }
    renderQueuePanel([]);

    scrollToBottomAndResume();
    // tail=50：对刚创建的任务，日志文件极短，tail=50 等同于从头读取，
    // 确保 SSE 能拿到日志头部元数据块（id/account/project 等）。
    startChatFollow(taskId, 0, 50);
  }

  return true;
}

// 发送消息及自动升级任务链
async function sendChatMessage() {
  const textarea = document.getElementById('chat-input');
  if (!textarea) return;
  let promptText = textarea.value.trim();
  if (!_hasChatInputContent()) return;
  if (!promptText && pendingImages.length > 0) {
    promptText = '请查看附件图片。';
  }

  if (chatUploadingImages > 0) {
    alert('图片还在上传中，请稍后再发送');
    return;
  }

  // 前端也检查队列是否已满（防止按钮状态不同步的情况）
  if (_isChatQueueFull(activeConversationId)) {
    alert(`队列已满（最多 ${CHAT_MAX_QUEUE} 条），请等待任务执行或删除队列中的任务后再发送`);
    return;
  }

  const sendBtn = document.getElementById('chat-send-btn');
  const autoMode = document.getElementById('chat-auto-mode')?.checked || false;
  const model = getChatModel();

  let executeAt = null;
  try {
    executeAt = getChatScheduleExecuteAt();
  } catch (e) {
    alert(e.message);
    return;
  }
  const schedCheckbox = document.getElementById('chat-schedule-checkbox');
  const schedTimeInput = document.getElementById('chat-sched-time');

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

    _setChatSendBusy(sendBtn, '建会话...');

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
          model,
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
      _autoTitleConversation(activeConversationId, promptText);
      const snapshotPaths = pendingImages.map(i => i.container_path);
      textarea.value = '';
      textarea.style.height = 'auto';
      pendingImages = [];
      renderImagePreviews();

      if (schedCheckbox) schedCheckbox.checked = false;
      toggleSchedTimeInput(false);
      if (schedTimeInput) schedTimeInput.value = '';

      sendBtn.dataset.busy = '';

      // 把新任务加入本地缓存，让队列面板立即读到（无需等待 loadConversations 回调）
      if (!tasksCache.find(t => t.id === taskData.id)) tasksCache.push(taskData);
      // 用服务端返回的实际状态决定占位态（比 _isProjectAccountBusy 更准确）
      const willPend1 = taskData.status === 'pending' || taskData.status === 'scheduled';
      // 清空占位文字，就地渲染用户气泡，避免整页重建导致的闪烁
      const chatContent = document.getElementById('chat-content');
      if (chatContent) chatContent.innerHTML = '';
      if (!_appendOptimisticUserMessage(promptText, snapshotPaths, taskData.id, willPend1, taskData.type)) {
        await selectConversation(activeConversationId);
      }
      loadConversations(false);
      loadTasks();
      loadProjectsDashboard();
      return;
    } catch (e) {
      alert('开启会话失败: ' + e.message);
      sendBtn.dataset.busy = '';
      _syncChatSendButton(currentChatTaskId, _isChatQueueFull(activeConversationId));
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
    _setChatSendBusy(sendBtn, '升级会话...');

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
      sendBtn.dataset.busy = '';
      _syncChatSendButton(currentChatTaskId, _isChatQueueFull(activeConversationId));
      return;
    }
  }

  // 3. 正常发送追问消息
  _setChatSendBusy(sendBtn, '发送中...');

  try {
    const r = await fetch(`${API}/api/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt: promptText,
        auto: autoMode,
        conversation_id: convId,
        images: pendingImages.map(i => i.container_path),
        model,
        execute_at: executeAt,
      })
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);

    const snapshotPaths = pendingImages.map(i => i.container_path);
    textarea.value = '';
    textarea.style.height = 'auto';
    pendingImages = [];
    renderImagePreviews();

    if (schedCheckbox) schedCheckbox.checked = false;
    toggleSchedTimeInput(false);
    if (schedTimeInput) schedTimeInput.value = '';

    sendBtn.dataset.busy = '';

    // 把新任务加入本地缓存，让队列面板立即读到（无需等待 loadConversations 回调）
    if (!tasksCache.find(t => t.id === data.id)) tasksCache.push(data);
    // 用服务端返回的实际状态决定占位态（比 _isProjectAccountBusy 更准确）
    const willPend = data.status === 'pending' || data.status === 'scheduled';
    // 就地追加气泡，避免整页重建导致的闪烁；fallback 到完整重载
    if (!_appendOptimisticUserMessage(promptText, snapshotPaths, data.id, willPend, data.type)) {
      await selectConversation(convId);
    }
    loadConversations(false);
    loadTasks();
    loadProjectsDashboard();
  } catch (e) {
    alert('发送消息失败: ' + e.message);
    sendBtn.dataset.busy = '';
    _syncChatSendButton(currentChatTaskId, _isChatQueueFull(activeConversationId));
  }
}

// 实时日志 SSE 追踪
// skipBytes: 客户端已渲染的字节数，传给服务端 skip_bytes 参数以精确跳过已有内容。
// tail: skipBytes=0 时回退的行数；乐观发送场景传 50 以捕获日志头部元数据块。
function startChatFollow(taskId, skipBytes = 0, tail = 0) {
  stopChatFollow();
  chatFollowMode = true;
  const qs = skipBytes > 0 ? `skip_bytes=${skipBytes}` : `tail=${tail}`;
  sseChatSource = new EventSource(sseUrl(`${API}/api/tasks/${taskId}/logs/stream?${qs}`));
  sseChatSource.onmessage = e => {
    if (e.data === '[DONE]') {
      // 任务结束时如果还留着未作答的 Intervention 实时表单，先禁用掉——虽然接下来
      // 的 selectConversation 全量重载会用 isRunning=false 重新渲染整个工作区（届时
      // 这类表单本来就不会再生成），但重载完成前这一小段时间不该让用户看到"看起来
      // 还能点"的表单。
      if (chatRenderer) chatRenderer.deactivatePendingInterventions();
      stopChatFollow();
      if (activeConversationId) {
        // 任务完成后刷新（会更新队列面板和头部按钮）
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
  if (!await confirmDialog('确定要永久删除该对话吗？任务记录将保留，但对话链不可恢复。', { danger: true })) return;
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

// 输入框有文字或有待发送图片，即视为"有内容可发送"
function _hasChatInputContent() {
  const textarea = document.getElementById('chat-input');
  return !!(textarea && textarea.value.trim()) || pendingImages.length > 0;
}

// 某会话排队中（pending/scheduled）的任务列表，按创建时间排序
// 一次性任务（"task-" 开头的伪会话 id）没有任务链排队概念，恒返回空
function _getQueuedChatTasks(convId) {
  if (!convId || convId.startsWith('task-')) return [];
  return (tasksCache || [])
    .filter(t => t.conversation_id === convId && (t.status === 'pending' || t.status === 'scheduled'))
    .sort((a, b) => new Date(a.created || 0) - new Date(b.created || 0));
}

function _isChatQueueFull(convId) {
  return _getQueuedChatTasks(convId).length >= CHAT_MAX_QUEUE;
}

// 发送按钮进入"请求进行中"态（建会话.../升级会话.../发送中.../上传中...）
function _setChatSendBusy(sendBtn, label) {
  sendBtn.dataset.busy = '1';
  sendBtn.disabled = true;
  sendBtn.classList.remove('chat-send-btn-loading');
  sendBtn.textContent = label;
}

// 发送/停止合并为同一个按钮：
// - 无运行中任务：始终显示"发送"。
// - 有运行中任务 + 输入框为空：显示加载中的转圈图标，点击即终止当前任务。
// - 有运行中任务 + 输入框有内容（或有待发送图片）：显示"发送"，点击追加消息进入排队，
//   而不会误触终止——用户既然在输入，多半是想接着说而非打断当前任务。
// runningTaskId 由调用方传入（而非在点击时读取可能已被轮询改写的全局变量），避免按钮
// 可见期间全局状态被其他任务/会话的同步覆盖，导致误终止。
// isQueueFull 仅在"发送"外观下生效；加载态本质是停止按钮，不受排队上限影响。
function _syncChatSendButton(runningTaskId, isQueueFull) {
  const btn = document.getElementById('chat-send-btn');
  if (!btn) return;
  if (btn.dataset.busy === '1') return; // 发送请求本身正在进行中（发送中.../建会话.../上传中...），由调用方自行管理文案
  const hasInput = _hasChatInputContent();
  if (runningTaskId && !hasInput) {
    btn.classList.add('chat-send-btn-loading');
    btn.innerHTML = '<span class="chat-send-spinner"></span>';
    btn.title = '点击终止当前任务';
    btn.disabled = false;
    btn.onclick = () => killChatTask(runningTaskId);
  } else {
    btn.classList.remove('chat-send-btn-loading');
    btn.textContent = '发送';
    btn.disabled = !!isQueueFull;
    btn.title = isQueueFull ? '队列已满，请等待执行或删除队列中的任务后再发送' : '';
    btn.onclick = () => sendChatMessage();
  }
}

async function killChatTask(taskId) {
  if (!await confirmDialog('确定要终止当前 AI 任务的执行吗？', { danger: true })) return;
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
  if (!await confirmDialog('确定要永久删除该一次性任务吗？任务记录与日志将全部被清除且不可恢复。', { danger: true })) return;
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

// ── 任务排队面板 ────────────────────────────────────────────

const CHAT_MAX_QUEUE = 3;

function formatQueueScheduleTime(iso) {
  if (!iso) return '时间待定';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '时间待定';

  const pad = n => String(n).padStart(2, '0');
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfTarget = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const dayDiff = Math.round((startOfTarget - startOfToday) / 86400000);
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`;

  if (dayDiff === 0) return `今天 ${time}`;
  if (dayDiff === 1) return `明天 ${time}`;
  return `${date.getMonth() + 1}/${date.getDate()} ${time}`;
}

function renderQueuePanel(pendingTasks) {
  const container = document.getElementById('chat-queue-panel');
  if (!container) return;
  if (!pendingTasks || pendingTasks.length === 0) {
    container.innerHTML = '';
    container.style.display = 'none';
    return;
  }

  const isFull = pendingTasks.length >= CHAT_MAX_QUEUE;
  const editIcon = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`;
  const trashIcon = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/></svg>`;

  let html = `<div class="chat-queue-header">
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
    等待队列 (${pendingTasks.length}/${CHAT_MAX_QUEUE})
    ${isFull ? `<span class="chat-queue-full-tip">队列已满，请执行或删除后再发送</span>` : ''}
  </div>`;

  pendingTasks.forEach((task, idx) => {
    const escapedPrompt = esc(task.prompt || '');
    const escapedId = esc(task.id);
    const isScheduled = task.status === 'scheduled';
    const statusLabel = isScheduled ? '定时' : '排队';
    const scheduleLabel = isScheduled ? `${formatQueueScheduleTime(task.execute_at)} 发送` : '';
    const statusTitle = isScheduled && task.execute_at
      ? `定时发送：${formatQueueScheduleTime(task.execute_at)}`
      : (isScheduled ? '定时待发送' : '等待上一条任务完成');
    html += `
    <div class="chat-queue-item" data-queue-task-id="${escapedId}">
      <span class="chat-queue-pos">${idx + 1}</span>
      <span class="chat-queue-status ${isScheduled ? 'scheduled' : 'pending'}" title="${esc(statusTitle)}">${statusLabel}</span>
      ${isScheduled ? `<span class="chat-queue-time" title="${esc(statusTitle)}">${esc(scheduleLabel)}</span>` : ''}
      <span class="chat-queue-text" title="${escapedPrompt}">${escapedPrompt}</span>
      <div class="chat-queue-actions">
        <button class="chat-queue-action-btn" title="编辑" onclick="startQueueItemEdit('${escapedId}', this)">${editIcon}</button>
        <button class="chat-queue-action-btn danger" title="删除" onclick="deleteQueuedTask('${escapedId}')">${trashIcon}</button>
      </div>
    </div>`;
  });

  container.innerHTML = html;
  container.style.display = 'block';
}

function startQueueItemEdit(taskId, btn) {
  const item = btn.closest('.chat-queue-item');
  if (!item) return;
  const textSpan = item.querySelector('.chat-queue-text');
  if (!textSpan) return;

  const currentText = textSpan.title || textSpan.textContent;
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'chat-queue-edit-input';
  input.value = currentText;
  textSpan.replaceWith(input);
  input.focus();
  input.select();
  _isEditingQueueItem = true;

  const saveEdit = async () => {
    _isEditingQueueItem = false;
    const newPrompt = input.value.trim();
    input.removeEventListener('blur', saveEdit);
    if (!newPrompt || newPrompt === currentText) {
      _refreshQueuePanel();
      return;
    }
    try {
      const r = await fetch(`${API}/api/tasks/${encodeURIComponent(taskId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: newPrompt }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        alert('修改失败: ' + (d.detail || r.statusText));
      } else {
        const t = tasksCache.find(t => t.id === taskId);
        if (t) t.prompt = newPrompt;
      }
    } catch (e) {
      alert('修改失败: ' + e.message);
    }
    _refreshQueuePanel();
  };

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); saveEdit(); }
    if (e.key === 'Escape') { _isEditingQueueItem = false; _refreshQueuePanel(); }
  });
  input.addEventListener('blur', saveEdit);
}

async function deleteQueuedTask(taskId) {
  try {
    const r = await fetch(`${API}/api/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      alert('删除失败: ' + (d.detail || r.statusText));
      return;
    }
    // Remove from tasksCache
    const idx = tasksCache.findIndex(t => t.id === taskId);
    if (idx !== -1) tasksCache.splice(idx, 1);
    _refreshQueuePanel();
    // Update sidebar + status text
    loadConversations(false);
    loadTasks();
  } catch (e) {
    alert('删除失败: ' + e.message);
  }
}

function _refreshQueuePanel() {
  if (!activeConversationId) return;
  if (_isEditingQueueItem) return;
  const convTasks = tasksCache.filter(t => t.conversation_id === activeConversationId);
  const pendingTasks = _getQueuedChatTasks(activeConversationId);
  renderQueuePanel(pendingTasks);

  // 同步发送/停止合并按钮（轮询期间任务状态可能在别处发生变化）
  const runningTasks = convTasks.filter(t => t.status === 'running');
  currentChatTaskId = runningTasks.length ? runningTasks[0].id : null;
  _syncChatSendButton(currentChatTaskId, pendingTasks.length >= CHAT_MAX_QUEUE);

  // 同步状态文字
  const statusText = document.getElementById('chat-status-text');
  if (statusText) {
    const runningCount = runningTasks.length;
    if (runningCount > 0) {
      let s = `<span style="color: var(--green); animation: pulse 1.5s infinite;">● AI 正在执行任务...</span>`;
      if (pendingTasks.length > 0) {
        s += ` <span style="color: var(--text-2); font-size: 11px;">(${pendingTasks.length}个任务在排队...)</span>`;
      }
      statusText.innerHTML = s;
    } else if (pendingTasks.length > 0) {
      statusText.innerHTML = `<span style="color: var(--text-2);">● 任务在排队中 (${pendingTasks.length}个)</span>`;
    } else {
      statusText.innerHTML = `<span style="color: var(--text-2);">就绪</span>`;
    }
  }
}

// ── 图片上传 ──────────────────────────────────────────────

function buildChatInputHTML(placeholder, hint) {
  const attachIcon = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M5 12h14"/></svg>`;
  return `
<div class="chat-input-container">
  <div id="chat-queue-panel" class="chat-queue-panel" style="display:none;"></div>
  <div class="chat-input-composer">
    <div id="chat-mention-dropdown" class="chat-mention-dropdown" style="display:none;"></div>
    <div id="chat-slash-dropdown" class="chat-mention-dropdown" style="display:none;"></div>
    <div id="chat-image-previews" class="chat-image-preview-row" style="display:none;"></div>
    <textarea class="chat-input-textarea" id="chat-input" placeholder="${placeholder}" rows="1"></textarea>
    <div id="chat-upload-status" class="chat-upload-status" style="display:none;"></div>
    <div class="chat-input-actions">
      <div class="chat-input-options">
        <input type="file" id="chat-file-input" multiple style="display:none" onchange="handleFileSelect(this)">
        <button class="chat-upload-btn" onclick="document.getElementById('chat-file-input').click()" title="附加文件，或直接粘贴截图">${attachIcon}</button>
        <label class="toggle-row" style="cursor: pointer;" title="全自动模式 (--dangerously-skip-permissions)">
          <input type="checkbox" id="chat-auto-mode" checked> 全自动
        </label>
        <label class="toggle-row" style="cursor: pointer;" title="选择未来时间，定时发送这条指令">
          <input type="checkbox" id="chat-schedule-checkbox" onchange="toggleSchedTimeInput(this.checked)"> 定时 ⏰
        </label>
        <span id="chat-sched-time-wrap" style="display: none; align-items: center; gap: 4px;">
          <input type="datetime-local" id="chat-sched-time" style="background: var(--surface-2); border: 1px solid var(--border-md); color: var(--text); border-radius: var(--radius); font-size: 11px; padding: 2px 4px; outline: none; min-height: 24px; width: auto;">
        </span>
        <span id="chat-status-text" class="chat-status-text">就绪</span>
      </div>
      <div class="chat-input-side">
        <div class="chat-input-hint">${hint}</div>
        <button class="btn primary chat-send-btn" id="chat-send-btn" onclick="sendChatMessage()">发送</button>
      </div>
    </div>
  </div>
</div>`;
}

// ── 模型选择（仅 Claude 类型账号支持 --model）────────────────

const CHAT_MODEL_OPTIONS = [
  { value: '',       label: '默认模型',        title: '跟随账号 / CLI 的默认模型设置' },
  { value: 'opus',   label: 'Opus 4.8 · 最强推理', title: 'Claude Opus 4.8 —— 推理能力最强，速度较慢，适合复杂任务' },
  { value: 'sonnet', label: 'Sonnet 5 · 均衡推荐', title: 'Claude Sonnet 5 —— 速度与能力均衡，日常任务首选' },
  { value: 'fable',  label: 'Fable 5 · 快速输出', title: 'Claude Fable 5 —— Opus 级能力、更快输出，Fast 模式使用' },
  { value: 'haiku',  label: 'Haiku 4.5 · 最快响应', title: 'Claude Haiku 4.5 —— 响应最快，适合简单或大批量任务' },
];

async function getChatAccount(accountName) {
  if (!accountName) return null;
  if (!globalAccountsCache.length) {
    try { globalAccountsCache = await fetch(`${API}/api/accounts`).then(r => r.json()); } catch { return null; }
  }
  return globalAccountsCache.find(a => a.name === accountName) || null;
}

async function getChatAccountType(accountName) {
  const acc = await getChatAccount(accountName);
  return acc?.type || '';
}

// 会话头部的套餐用量徽章（Claude Max / ChatGPT(Codex) 账号、且探测到过数据时显示）
function buildChatUsagePillHtml(account) {
  const usage = account?.usage;
  const w = usage?.five_hour;
  if (!account || !usage || usage.error || !w || w.utilization == null) return '';
  const pct = Math.max(0, Math.min(100, Math.round(w.utilization)));
  const fillClass = pct >= 90 ? 'usage-fill usage-danger' : pct >= 75 ? 'usage-fill usage-warn' : 'usage-fill';
  const sevenDay = usage.seven_day;
  const titleParts = [`5h 窗口已用 ${pct}%`];
  if (w.resets_at) titleParts.push(`重置：${new Date(w.resets_at).toLocaleString()}`);
  if (sevenDay?.utilization != null) titleParts.push(`7d 已用 ${Math.round(sevenDay.utilization)}%`);
  titleParts.push('点击刷新');
  return `
      <span class="chat-usage-pill" id="chat-usage-pill" title="${esc(titleParts.join(' · '))}"
            onclick="refreshChatUsage('${esc(account.name)}',event)">
        <span class="chat-model-pill-label">5h 用量</span>
        <span class="chat-usage-track"><span class="${fillClass}" style="width:${pct}%"></span></span>
        <span style="font-size:11px;color:var(--text-2)">${pct}%</span>
      </span>`;
}

async function refreshChatUsage(accountName, event) {
  event?.stopPropagation();
  const pill = document.getElementById('chat-usage-pill');
  if (pill) pill.style.opacity = '.5';
  try {
    await fetch(`${API}/api/accounts/${encodeURIComponent(accountName)}/usage/refresh`, { method: 'POST' });
  } catch { /* 探测失败也重新拉一次账号列表，把 error 状态展示出来 */ }
  try {
    globalAccountsCache = await fetch(`${API}/api/accounts`).then(r => r.json());
  } catch { return; }
  if (activeConversationId) selectConversation(activeConversationId);
}

function buildChatModelPillHtml() {
  const opts = CHAT_MODEL_OPTIONS.map(o =>
    `<option value="${esc(o.value)}" title="${esc(o.title)}">${esc(o.label)}</option>`
  ).join('');
  return `
      <span class="chat-model-pill" title="选择本次对话使用的模型">
        <span class="chat-model-pill-label">模型</span>
        <select id="chat-model-select" class="chat-model-select" onchange="onChatModelChange(this.value)">${opts}</select>
      </span>`;
}

function onChatModelChange(value) {
  if (activeConversationId) chatModelByConversation[activeConversationId] = value;
}

function getChatModel() {
  return document.getElementById('chat-model-select')?.value || '';
}

function toggleSchedTimeInput(checked) {
  const el = document.getElementById('chat-sched-time-wrap');
  if (el) {
    el.style.display = checked ? 'inline-flex' : 'none';
  }
  if (checked) ensureChatScheduleDefault();
}

function formatChatDatetimeLocal(date) {
  const pad = n => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function ensureChatScheduleDefault() {
  const input = document.getElementById('chat-sched-time');
  if (!input) return;
  const minDate = new Date(Date.now() + 60 * 1000);
  const minValue = formatChatDatetimeLocal(minDate);
  input.min = minValue;
  if (!input.value || new Date(input.value) <= new Date()) {
    const defaultDate = new Date(Date.now() + 5 * 60 * 1000);
    input.value = formatChatDatetimeLocal(defaultDate);
  }
}

function getChatScheduleExecuteAt() {
  const schedCheckbox = document.getElementById('chat-schedule-checkbox');
  if (!schedCheckbox || !schedCheckbox.checked) return null;

  const input = document.getElementById('chat-sched-time');
  if (!input || !input.value) {
    throw new Error('请选择定时发送时间');
  }

  const selected = new Date(input.value);
  if (Number.isNaN(selected.getTime()) || selected <= new Date()) {
    throw new Error('定时发送时间必须晚于当前时间');
  }

  return input.value.includes(':') && input.value.split(':').length === 2
    ? `${input.value}:00`
    : input.value;
}

async function handleFileSelect(input) {
  const files = Array.from(input.files);
  input.value = '';
  if (!files.length) return;
  await uploadChatFiles(files);
}

async function handleChatPaste(event) {
  const items = Array.from(event.clipboardData?.items || []);
  const files = items
    .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
    .map(item => item.getAsFile())
    .filter(Boolean);

  if (!files.length) return;
  event.preventDefault();
  await uploadChatFiles(files, true);
}

async function uploadChatFiles(files, fromPaste = false) {
  if (!currentChatProjectName) {
    alert('请先从左侧选择项目后再上传文件');
    return;
  }

  // Phase 2: 复用共享的 uploadFilesForCompose（消除与 mobile 的重复 wrapper）
  await uploadFilesForCompose(files, fromPaste, {
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

// normalizeFileForUpload 已迁移至 shared/image-upload.js（全局可用）

function updateChatUploadState() {
  const sendBtn = document.getElementById('chat-send-btn');
  const status = document.getElementById('chat-upload-status');
  if (sendBtn) {
    if (chatUploadingImages > 0) {
      _setChatSendBusy(sendBtn, '上传中...');
    } else {
      sendBtn.dataset.busy = '';
      _syncChatSendButton(currentChatTaskId, _isChatQueueFull(activeConversationId));
    }
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
    getImages: () => pendingImages,
    removeImage: (index) => pendingImages.splice(index, 1),
    clearImages: () => { pendingImages = []; },
    afterRender: () => { if (currentChatTaskId) _syncChatSendButton(currentChatTaskId, _isChatQueueFull(activeConversationId)); },
    getSseUrl: (u) => sseUrl(u),
    getApi: () => (typeof API !== 'undefined' ? API : ''),
    escFn: (s) => esc(s),
  });
}

// removePendingImage / clearPendingImages 由 shared 提供（全局）
// 它们会自动回退调用 renderImagePreviews（window 上已存在）

// renderTaskFileAttachments 由 shared/image-upload.js 提供（全局可用）

// ── 最近访问区 ────────────────────────────────────────────

function renderRecentSection(convs, projects, tasks) {
  const allItems = [];

  convs.forEach(c => {
    const convTasks = tasks.filter(t => t.conversation_id === c.id)
      .sort((a, b) => new Date(b.created || 0) - new Date(a.created || 0));
    const latestStatus = convTasks[0]?.status || 'done';
    const proj = projects.find(p => conversationBelongsToProject(c, p));
    allItems.push({
      type: 'conversation',
      id: c.id,
      name: c.name || c.id,
      projectName: proj?.name || c.project_name || '',
      time: c.updated || c.created || 0,
      status: latestStatus,
    });
  });

  tasks.filter(t => !t.conversation_id && !t.archived).forEach(t => {
    const proj = projects.find(p => taskBelongsToProject(t, p));
    allItems.push({
      type: 'one-off',
      id: `task-${t.id}`,
      name: t.prompt,
      projectName: proj?.name || t.project_name || '',
      time: t.created || 0,
      status: t.status || 'done',
    });
  });

  allItems.sort((a, b) => new Date(b.time) - new Date(a.time));
  const recentItems = allItems.slice(0, 5);
  if (!recentItems.length) return '';

  const isCollapsed = localStorage.getItem('coderfleet.recentSectionCollapsed') === 'true';

  let html = `<div class="recent-section${isCollapsed ? ' collapsed' : ''}">
    <div class="recent-header" onclick="toggleRecentSection()">
      <span class="recent-header-label">最近访问</span>
      <span class="recent-collapse-icon">${isCollapsed ? '▸' : '▾'}</span>
    </div>
    <div class="recent-items">`;

  recentItems.forEach(item => {
    const isActive = item.id === activeConversationId;
    const isRunning = item.status === 'running';
    const isPending = item.status === 'pending' || item.status === 'scheduled';
    const displayTime = fmtTimeFriendly(item.time);
    const statusBadge = isRunning
      ? `<span class="session-status-dot running" title="运行中"></span>`
      : isPending
      ? `<span class="session-status-dot pending"></span>`
      : `<span class="session-time">${esc(displayTime)}</span>`;
    html += `<div class="chat-session-item recent-item${isActive ? ' active' : ''}" data-item-id="${esc(item.id)}" onclick="selectConversation('${esc(item.id)}')">
        <div class="recent-item-body">
          <span class="recent-project-chip">${esc(item.projectName)}</span>
          <span class="session-name" title="${esc(item.name)}">${esc(item.name)}</span>
        </div>
        ${statusBadge}
      </div>`;
  });

  html += `</div></div><div class="recent-divider"></div>`;
  return html;
}

function toggleRecentSection() {
  const collapsed = localStorage.getItem('coderfleet.recentSectionCollapsed') === 'true';
  localStorage.setItem('coderfleet.recentSectionCollapsed', collapsed ? 'false' : 'true');
  rerenderChatProjectList();
}

// ── Ctrl+K 快速跳转 ───────────────────────────────────────

let quickJumpItems = [];
let quickJumpQuery = '';
let quickJumpSelectedIdx = 0;

function openQuickJump() {
  _buildQuickJumpItems();
  quickJumpQuery = '';
  quickJumpSelectedIdx = 0;
  const overlay = document.getElementById('quick-jump-overlay');
  const input = document.getElementById('quick-jump-input');
  if (!overlay || !input) return;
  overlay.style.display = 'flex';
  input.value = '';
  _renderQuickJumpResults();
  requestAnimationFrame(() => input.focus());
}

function closeQuickJump() {
  const overlay = document.getElementById('quick-jump-overlay');
  if (overlay) overlay.style.display = 'none';
}

function _buildQuickJumpItems() {
  const convs = chatConversationsList;
  const tasks = tasksCache;
  const projects = projectsCache;
  quickJumpItems = [];

  convs.forEach(c => {
    const convTasks = tasks.filter(t => t.conversation_id === c.id)
      .sort((a, b) => new Date(b.created || 0) - new Date(a.created || 0));
    const latestStatus = convTasks[0]?.status || 'done';
    const proj = projects.find(p => conversationBelongsToProject(c, p));
    quickJumpItems.push({
      type: 'conversation',
      id: c.id,
      name: c.name || c.id,
      projectName: proj?.name || c.project_name || '',
      time: c.updated || c.created || 0,
      status: latestStatus,
      searchText: ((c.name || '') + ' ' + (proj?.name || c.project_name || '')).toLowerCase(),
    });
  });

  tasks.filter(t => !t.conversation_id && !t.archived).forEach(t => {
    const proj = projects.find(p => taskBelongsToProject(t, p));
    quickJumpItems.push({
      type: 'one-off',
      id: `task-${t.id}`,
      name: t.prompt,
      projectName: proj?.name || t.project_name || '',
      time: t.created || 0,
      status: t.status || 'done',
      searchText: ((t.prompt || '') + ' ' + (proj?.name || t.project_name || '')).toLowerCase(),
    });
  });

  quickJumpItems.sort((a, b) => new Date(b.time) - new Date(a.time));
}

function filterQuickJump(value) {
  quickJumpQuery = value;
  quickJumpSelectedIdx = 0;
  _renderQuickJumpResults();
}

function _getQuickJumpFiltered() {
  const q = quickJumpQuery.trim().toLowerCase();
  return q
    ? quickJumpItems.filter(item => item.searchText.includes(q)).slice(0, 20)
    : quickJumpItems.slice(0, 20);
}

function _renderQuickJumpResults() {
  const container = document.getElementById('quick-jump-results');
  if (!container) return;
  const filtered = _getQuickJumpFiltered();

  if (!filtered.length) {
    container.innerHTML = `<div class="quick-jump-empty">无匹配结果</div>`;
    return;
  }

  container.innerHTML = filtered.map((item, i) => {
    const isSelected = i === quickJumpSelectedIdx;
    const isRunning = item.status === 'running';
    const dot = isRunning ? `<span class="session-status-dot running"></span>` : '';
    return `<div class="quick-jump-item${isSelected ? ' selected' : ''}" data-idx="${i}" onmouseenter="quickJumpSelectedIdx=${i}; document.querySelectorAll('#quick-jump-results .quick-jump-item').forEach((el,j)=>el.classList.toggle('selected',j===${i}))" onclick="selectQuickJumpItem(${i})">
        <span class="quick-jump-project-chip">${esc(item.projectName)}</span>
        <span class="quick-jump-item-name">${esc(item.name)}</span>
        ${dot}
      </div>`;
  }).join('');

  const selectedEl = container.querySelector('.quick-jump-item.selected');
  if (selectedEl) selectedEl.scrollIntoView({ block: 'nearest' });
}

function selectQuickJumpItem(idx) {
  const filtered = _getQuickJumpFiltered();
  const item = filtered[idx];
  if (!item) return;
  closeQuickJump();
  if (currentPage !== 'chat') showPage('chat');
  selectConversation(item.id);
}

document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
    const tag = document.activeElement?.tagName?.toUpperCase();
    const activeId = document.activeElement?.id;
    if (activeId === 'quick-jump-input') return;
    if (tag === 'TEXTAREA' || tag === 'INPUT') return;
    e.preventDefault();
    const overlay = document.getElementById('quick-jump-overlay');
    if (overlay && overlay.style.display !== 'none') {
      closeQuickJump();
    } else {
      openQuickJump();
    }
    return;
  }

  const overlay = document.getElementById('quick-jump-overlay');
  if (!overlay || overlay.style.display === 'none') return;

  if (e.key === 'Escape') { e.preventDefault(); closeQuickJump(); return; }
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    quickJumpSelectedIdx = Math.min(quickJumpSelectedIdx + 1, _getQuickJumpFiltered().length - 1);
    _renderQuickJumpResults();
    return;
  }
  if (e.key === 'ArrowUp') {
    e.preventDefault();
    quickJumpSelectedIdx = Math.max(quickJumpSelectedIdx - 1, 0);
    _renderQuickJumpResults();
    return;
  }
  if (e.key === 'Enter') { e.preventDefault(); selectQuickJumpItem(quickJumpSelectedIdx); return; }
});

// ── 临时任务弹窗 ──────────────────────────────────────────────

async function openEphemeralModalForProject(projectName, accountName) {
  await openEphemeralModal();
  if (accountName) {
    const sel = document.getElementById('eph-account');
    if (sel) {
      for (const opt of sel.options) {
        if (opt.value === accountName) { sel.value = accountName; break; }
      }
    }
  }
  if (projectName) {
    const convInput = document.getElementById('eph-conv-name');
    if (convInput && !convInput.value) convInput.value = projectName;
  }
}

async function openEphemeralModal() {
  const modal = document.getElementById('ephemeral-modal');
  if (!modal) return;

  // Load account options
  const sel = document.getElementById('eph-account');
  try {
    const accounts = await fetch(`${API}/api/accounts`).then(r => r.json());
    sel.innerHTML = accounts.map(a =>
      `<option value="${esc(a.name)}">${esc(a.name)} (${esc(a.type)})</option>`
    ).join('');
  } catch (e) {
    sel.innerHTML = '<option value="">加载失败</option>';
  }

  document.getElementById('eph-msg').style.display = 'none';
  modal.style.display = '';
  setTimeout(() => document.getElementById('eph-prompt').focus(), 50);
}

function closeEphemeralModal(e) {
  if (e && e.target !== document.getElementById('ephemeral-modal')) return;
  document.getElementById('ephemeral-modal').style.display = 'none';
}

async function submitEphemeralTask() {
  const account = document.getElementById('eph-account').value;
  const prompt  = document.getElementById('eph-prompt').value.trim();
  const secretsRaw = document.getElementById('eph-secrets').value.trim();
  const outputDir  = document.getElementById('eph-output-dir').value.trim();
  const convName   = document.getElementById('eph-conv-name').value.trim();
  const retention  = document.getElementById('eph-retention')?.value || 'release_on_finish';
  const ttlMinutes = parseInt(document.getElementById('eph-ttl-minutes')?.value || '120', 10);
  const auto       = document.getElementById('eph-auto').checked;
  const msg        = document.getElementById('eph-msg');
  const btn        = document.getElementById('eph-submit-btn');

  if (!account) { alert('请选择账号'); return; }
  if (!prompt)  { alert('请填写任务描述'); return; }

  // Parse secrets KEY=VAL lines
  const secrets = {};
  for (const line of secretsRaw.split('\n')) {
    const l = line.trim();
    if (!l || l.startsWith('#')) continue;
    const idx = l.indexOf('=');
    if (idx < 1) { alert(`Secrets 格式错误：${l}\n应为 KEY=VAL`); return; }
    secrets[l.slice(0, idx).trim()] = l.slice(idx + 1);
  }

  btn.disabled = true;
  btn.textContent = '提交中...';
  msg.style.display = 'none';

  const body = { prompt, account, auto, ephemeral: true, secrets };
  if (outputDir)  body.output_dir = outputDir;
  if (convName)   body.conversation_name = convName;
  body.ephemeral_retention = retention;
  body.ephemeral_ttl_minutes = Number.isFinite(ttlMinutes) ? ttlMinutes : 120;

  try {
    const r = await fetch(`${API}/api/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);

    // Reset form fields (keep account)
    document.getElementById('eph-prompt').value = '';
    document.getElementById('eph-secrets').value = '';
    document.getElementById('eph-output-dir').value = '';
    document.getElementById('eph-conv-name').value = '';
    const retentionEl = document.getElementById('eph-retention');
    if (retentionEl) retentionEl.value = 'release_on_finish';
    const ttlEl = document.getElementById('eph-ttl-minutes');
    if (ttlEl) ttlEl.value = '120';

    // Close modal and navigate directly to the task in the main chat area
    // so the user sees execution progress inline (not in a disposable popup).
    closeEphemeralModal();
    const navId = data.conversation_id || `task-${data.id}`;
    selectConversation(navId);
    if (typeof loadTasks === 'function') loadTasks();
  } catch (e) {
    msg.style.display = '';
    msg.innerHTML = `<div style="color:var(--red)">提交失败：${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '提交任务';
  }
}
// 直接调用即可，此处无需再定义包装函数（否则会覆盖 window.renderTaskFileAttachments，导致无限递归）
