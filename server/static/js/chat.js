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

    conversationsCache = {};
    convs.forEach(c => { conversationsCache[c.id] = c.name; });

    renderConversations(convs, projects, tasks);

    if (renderWorkspace) {
      if (!activeConversationId) {
        renderEmptyChatState();
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
        } else {
          startNewChat();
        }
      } else {
        const active = convs.find(c => c.id === activeConversationId);
        if (active) {
          renderChatWorkspace(active);
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

  projects.forEach(proj => {
    const projConvs = convs.filter(c => conversationBelongsToProject(c, proj));
    const projTasks = tasks.filter(t => !t.conversation_id && taskBelongsToProject(t, proj));

    const items = [];
    projConvs.forEach(c => {
      items.push({
        type: 'conversation',
        id: c.id,
        name: c.name || c.id,
        time: c.updated || c.created || 0
      });
    });
    projTasks.forEach(t => {
      items.push({
        type: 'one-off',
        id: `task-${t.id}`,
        name: t.prompt,
        time: t.created || 0
      });
    });

    items.sort((a, b) => new Date(b.time) - new Date(a.time));

    html += `
  <div class="chat-project-group">
    <div class="chat-project-header">
      <div class="proj-header-title" title="${esc(proj.name)}">
        ${folderSvg}
        <span>${esc(proj.name)}</span>
      </div>
      <button class="proj-new-chat-btn" onclick="event.stopPropagation(); startNewChat({ projectName: '${esc(proj.name)}' })" title="在 ${esc(proj.name)} 中开始新对话">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
          <path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
        </svg>
      </button>
    </div>
    <div class="chat-project-items">
`;

    if (items.length === 0) {
      html += `<div class="chat-project-empty">暂无对话</div>`;
    } else {
      html += items.map(item => {
        const isActive = item.id === activeConversationId;
        const displayTime = fmtTimeFriendly(item.time);
        const archiveBtn = item.type === 'conversation'
          ? `<button class="session-action-btn" title="归档" onclick="event.stopPropagation(); archiveConversation('${esc(item.id)}')">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="21 8 21 21 3 21 3 8"></polyline><rect x="1" y="3" width="22" height="5"></rect><line x1="10" y1="12" x2="14" y2="12"></line></svg>
             </button>`
          : (item.type === 'one-off'
            ? `<button class="session-action-btn" title="归档" onclick="event.stopPropagation(); archiveOneOff('${esc(item.id)}')">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="21 8 21 21 3 21 3 8"></polyline><rect x="1" y="3" width="22" height="5"></rect><line x1="10" y1="12" x2="14" y2="12"></line></svg>
               </button>`
            : '');
        const deleteBtn = item.type === 'conversation'
          ? `<button class="session-action-btn danger" title="删除" onclick="event.stopPropagation(); deleteConversation('${esc(item.id)}')">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14H6L5 6"></path><path d="M10 11v6"></path><path d="M14 11v6"></path><path d="M9 6V4h6v2"></path></svg>
             </button>`
          : (item.type === 'one-off'
            ? `<button class="session-action-btn danger" title="删除" onclick="event.stopPropagation(); deleteOneOff('${esc(item.id)}')">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6l-1 14H6L5 6"></path><path d="M10 11v6"></path><path d="M14 11v6"></path><path d="M9 6V4h6v2"></path></svg>
               </button>`
            : '');
        return `
      <div class="chat-session-item ${isActive ? 'active' : ''}" onclick="selectConversation('${esc(item.id)}')">
        <span class="session-name" title="${esc(item.name)}">${esc(item.name)}</span>
        <span class="session-time">${esc(displayTime)}</span>
        <span class="session-actions">${archiveBtn}${deleteBtn}</span>
      </div>`;
      }).join('');
    }

    html += `
    </div>
  </div>
`;
  });

  list.innerHTML = html;
}

// 选择会话
async function selectConversation(convId) {
  stopChatFollow();
  activeConversationId = convId;
  await loadConversations();
}

// 开启新会话
function startNewChat(options = {}) {
  stopChatFollow();
  activeConversationId = null;
  currentChatTaskId = null;
  chatNewSessionProject = options.projectName || '';
  showPage('chat');
  loadConversations();
}

// 渲染新会话空状态（无历史记录的初始界面）
function renderEmptyChatState() {
  const workspace = document.getElementById('chat-workspace');
  if (!workspace) return;

  const projectLabel = chatNewSessionProject || '未指定项目';

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

<div class="chat-input-container">
  <div class="chat-input-row">
    <textarea class="chat-input-textarea" id="chat-input" placeholder="输入您的开发指令... (按 Enter 发送，Shift+Enter 换行)" rows="1"></textarea>
    <button class="btn primary" id="chat-send-btn" onclick="sendChatMessage()" style="min-height: 40px;">发送</button>
  </div>
  <div class="chat-input-actions">
    <div class="chat-input-options">
      <label class="toggle-row" style="cursor: pointer;">
        <input type="checkbox" id="chat-auto-mode" checked> 全自动模式 (--dangerously-skip-permissions)
      </label>
      <span style="color: var(--border-md);">|</span>
      <span id="chat-status-text" style="color: var(--text-2);">就绪</span>
    </div>
    <div style="font-size: 11px; color: var(--text-3);">发送第一条消息后将自动创建会话链</div>
  </div>
</div>
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
    <!-- 动态渲染终止和刷新按钮 -->
  </div>
</div>

<!-- 对话渲染区 -->
<div class="chat-main-viewport" id="chat-viewport">
  <div id="chat-content"></div>
</div>

<!-- 底部输入框 -->
<div class="chat-input-container">
  <div class="chat-input-row">
    <textarea class="chat-input-textarea" id="chat-input" placeholder="输入您下一轮的指令... (按 Enter 发送，Shift+Enter 换行)" rows="1"></textarea>
    <button class="btn primary" id="chat-send-btn" onclick="sendChatMessage()" style="min-height: 40px;">发送</button>
  </div>
  <div class="chat-input-actions">
    <div class="chat-input-options">
      <label class="toggle-row" style="cursor: pointer;">
        <input type="checkbox" id="chat-auto-mode" checked> 全自动模式 (--dangerously-skip-permissions)
      </label>
      <span style="color: var(--border-md);">|</span>
      <span id="chat-status-text" style="color: var(--text-2);">就绪</span>
    </div>
    <div style="font-size: 11px; color: var(--text-3);">${conv.isOneOff ? '一次性单任务 (发送消息升级为任务链)' : 'AI 连续问答 · 会话链模式'}</div>
  </div>
</div>
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

    // 渲染头部动作按钮
    const headerActions = document.getElementById('chat-header-actions');
    const runningTask = convTasks.find(t => t.status === 'running');
    if (runningTask) {
      currentChatTaskId = runningTask.id;
      headerActions.innerHTML = `
    <button class="btn danger" onclick="killChatTask('${runningTask.id}')">终止执行</button>
    <button class="btn" onclick="selectConversation('${conv.id}')">刷新</button>
  `;
      document.getElementById('chat-status-text').innerHTML = `<span style="color: var(--green); animation: pulse 1.5s infinite;">● AI 正在执行任务...</span>`;
      document.getElementById('chat-input').disabled = true;
      document.getElementById('chat-send-btn').disabled = true;
    } else {
      currentChatTaskId = null;
      headerActions.innerHTML = `
    <button class="btn" onclick="selectConversation('${conv.id}')">刷新</button>
  `;
    }

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
      <div class="user-bubble-title">你:</div>
      <div class="user-bubble-content">${esc(task.prompt)}</div>
    </div>`;
      chatContent.appendChild(userWrap);

      // 2. 渲染日志输出的容器
      const logWrap = document.createElement('div');
      logWrap.style.marginBottom = '24px';
      chatContent.appendChild(logWrap);

      // 3. 构建局部的 ChatLogRenderer 并进行渲染
      // 每一个任务在渲染时，均传入 foldProcess=true，把中间执行步骤折叠起来，把回复直接展现
      const localRenderer = new ChatLogRenderer(logWrap, task.status === 'running', true);
      localRenderer.render(logText);

      // 4. 如果是最后一个任务，把该 localRenderer 赋给全局 chatRenderer 方便 SSE 追加
      if (idx === convTasks.length - 1) {
        chatRenderer = localRenderer;
      }
    });

    scrollChatViewportToBottom();

    // 如果有正在运行的任务，开启实时追踪
    if (runningTask) {
      startChatFollow(runningTask.id);
    }
  } catch (e) {
    chatContent.innerHTML = `<div style="color:var(--red);padding:16px">加载会话历史失败: ${esc(e.message)}</div>`;
  }
}

// 发送消息及自动升级任务链
async function sendChatMessage() {
  const textarea = document.getElementById('chat-input');
  if (!textarea) return;
  const promptText = textarea.value.trim();
  if (!promptText) return;

  const sendBtn = document.getElementById('chat-send-btn');
  const autoMode = document.getElementById('chat-auto-mode')?.checked || false;

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
          conversation_name: conversationName
        })
      });
      const taskData = await rTask.json();
      if (!rTask.ok) throw new Error(taskData.detail || rTask.statusText);

      if (!taskData.conversation_id) {
        throw new Error('后端未返回会话 ID');
      }

      activeConversationId = taskData.conversation_id;
      textarea.value = '';
      textarea.style.height = 'auto';

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
    } catch (e) {
      alert('升级任务链失败: ' + e.message);
      sendBtn.disabled = false;
      sendBtn.textContent = '发送';
      return;
    }
  }

  // 3. 正常发送追问消息
  textarea.disabled = true;
  sendBtn.disabled = true;
  sendBtn.textContent = '发送中...';

  try {
    const r = await fetch(`${API}/api/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt: promptText,
        auto: autoMode,
        conversation_id: convId
      })
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);

    textarea.value = '';
    textarea.style.height = 'auto';

    await selectConversation(convId);
    loadTasks();
    loadProjectsDashboard();
  } catch (e) {
    alert('发送消息失败: ' + e.message);
    textarea.disabled = false;
    sendBtn.disabled = false;
    sendBtn.textContent = '发送';
  }
}

// 实时日志 SSE 追踪
function startChatFollow(taskId) {
  stopChatFollow();
  chatFollowMode = true;
  sseChatSource = new EventSource(`${API}/api/tasks/${taskId}/logs/stream?tail=0`);
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

