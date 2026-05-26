// ── 工作流 DAG 视图 ───────────────────────────────────────

const DAG_NODE_W  = 220;
const DAG_NODE_H  = 100;
const DAG_H_GAP   = 60;
const DAG_V_GAP   = 80;
const DAG_PAD     = 48;

// ══════════════════════════════════════════════════════════════
//  加载 / 刷新
// ══════════════════════════════════════════════════════════════
async function loadWorkflows() {
  // Respect active tab: delegate to templates loader if needed
  if (wfActiveTab === 'templates') {
    // Ensure correct panel visibility
    const rp = document.getElementById('wf-panel-runs');
    const tp = document.getElementById('wf-panel-templates');
    if (rp) rp.style.display = 'none';
    if (tp) tp.style.display = '';
    document.getElementById('wf-tab-runs')?.classList.remove('active');
    document.getElementById('wf-tab-templates')?.classList.add('active');
    return loadTemplates();
  }

  // 从 localStorage 恢复上次打开的工作流
  if (!activePipelineId) {
    activePipelineId = localStorage.getItem('coderfleet.activePipelineId') || null;
  }

  try {
    const [pipelines, tasks] = await Promise.all([
      fetch(`${API}/api/pipelines`).then(r => r.json()),
      fetch(`${API}/api/tasks?limit=300`).then(r => r.json()).catch(() => []),
    ]);
    const templatePipelines = pipelines.filter(p => p.template_id);
    pipelinesCache     = templatePipelines;
    workflowTasksCache = tasks;
    renderPipelineList(templatePipelines, tasks);

    if (activePipelineId) {
      const p = templatePipelines.find(p => p.id === activePipelineId);
      if (p) {
        _patchDagIfRendered(p, tasks);
      } else {
        activePipelineId = null;
        localStorage.removeItem('coderfleet.activePipelineId');
        _renderDagEmpty();
        _hideToolbar();
      }
    } else {
      _renderDagEmpty();
      _hideToolbar();
    }
  } catch (e) {
    console.error('loadWorkflows:', e);
  }
}

// ══════════════════════════════════════════════════════════════
//  过滤器
// ══════════════════════════════════════════════════════════════
function setRunsFilter(f) {
  wfRunsFilter = f;
  document.querySelectorAll('#wf-runs-filter .wf-filter-pill').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('onclick').includes(`'${f}'`));
  });
  renderPipelineList(pipelinesCache, workflowTasksCache);
}

// ══════════════════════════════════════════════════════════════
//  Pipeline 列表侧边栏
// ══════════════════════════════════════════════════════════════
function renderPipelineList(pipelines, tasks) {
  const list = document.getElementById('pipeline-list');
  if (!list) return;

  // 根据过滤条件筛选
  let filtered = pipelines;
  if (wfRunsFilter !== 'all') {
    filtered = pipelines.filter(p => {
      const pTasks = tasks.filter(t => p.task_ids.includes(t.id));
      if (wfRunsFilter === 'running') return pTasks.some(t => t.status === 'running');
      if (wfRunsFilter === 'failed')  return pTasks.some(t => t.status === 'failed');
      if (wfRunsFilter === 'done')    return pTasks.length > 0 && pTasks.every(t => t.status === 'done');
      return true;
    });
  }

  if (!filtered.length) {
    const hint = wfRunsFilter === 'all'
      ? '暂无模板运行记录<br><span style="color:var(--text-3);font-size:12px">从模板库运行模板后会出现在这里</span>'
      : `没有符合「${({running:'运行中',failed:'失败',done:'完成'})[wfRunsFilter]}」条件的记录`;
    list.innerHTML = `<div class="empty" style="padding:20px 14px;font-size:13px">${hint}</div>`;
    return;
  }

  list.innerHTML = filtered.map(p => {
    const pTasks = tasks.filter(t => p.task_ids.includes(t.id));
    const running = pTasks.some(t => t.status === 'running');
    const failed  = pTasks.some(t => t.status === 'failed');
    const allDone = pTasks.length > 0 && pTasks.every(t => t.status === 'done');
    const dot = running ? `<span class="status-dot running"  style="font-size:10px">运行中</span>`
              : failed  ? `<span class="status-dot failed"   style="font-size:10px">失败</span>`
              : allDone ? `<span class="status-dot done"     style="font-size:10px">完成</span>`
              : `<span style="color:var(--text-3);font-size:10px">待执行</span>`;

    const isActive = activePipelineId === p.id;
    return `<div class="pipeline-list-item${isActive ? ' active' : ''}" onclick="openPipeline('${esc(p.id)}')">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:6px">
        <span style="font-size:13px;font-weight:500;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.name)}</span>
        ${dot}
      </div>
      <div style="font-size:11px;color:var(--text-3);margin-top:2px">${pTasks.length} 个任务 · ${fmtTime(p.updated || p.created)}</div>
    </div>`;
  }).join('');
}

// ══════════════════════════════════════════════════════════════
//  打开工作流
// ══════════════════════════════════════════════════════════════
async function openPipeline(id) {
  activePipelineId = id;
  localStorage.setItem('coderfleet.activePipelineId', id);
  workflowSelectedTaskId = null;
  dagZoom = 1.0;
  closeWorkflowDetail();

  try {
    const [pipeline, tasks] = await Promise.all([
      fetch(`${API}/api/pipelines/${id}`).then(r => r.json()),
      fetch(`${API}/api/tasks?limit=300`).then(r => r.json()).catch(() => []),
    ]);
    workflowTasksCache = tasks;
    pipelinesCache = pipelinesCache.map(p => p.id === id ? pipeline : p);
    renderPipelineList(pipelinesCache, tasks);
    _renderActivePipeline(pipeline, tasks);
  } catch (e) {
    const area = document.getElementById('dag-area');
    if (area) area.innerHTML = `<div class="dag-empty"><span style="color:var(--red)">加载失败：${esc(e.message)}</span></div>`;
  }
}

function _renderActivePipeline(pipeline, allTasks) {
  const pTasks = allTasks.filter(t => pipeline.task_ids.includes(t.id));
  _showToolbar(pipeline);
  renderDag(pipeline, pTasks);
}

// ══════════════════════════════════════════════════════════════
//  DAG 布局算法
// ══════════════════════════════════════════════════════════════
function _computeDagLayout(tasks) {
  const taskMap = new Map(tasks.map(t => [t.id, t]));
  const levels  = new Map();

  function getLevel(id, visited = new Set()) {
    if (visited.has(id)) return 0;
    if (levels.has(id)) return levels.get(id);
    const t = taskMap.get(id);
    if (!t) return 0;
    visited.add(id);
    const deps = [...(t.depends_on || [])];
    if (t.parent_task_id && taskMap.has(t.parent_task_id) && !deps.includes(t.parent_task_id))
      deps.push(t.parent_task_id);
    const localDeps = deps.filter(d => taskMap.has(d));
    if (!localDeps.length) { levels.set(id, 0); return 0; }
    const lv = Math.max(...localDeps.map(d => getLevel(d, new Set(visited)))) + 1;
    levels.set(id, lv);
    return lv;
  }
  tasks.forEach(t => getLevel(t.id));

  const byLevel = new Map();
  for (const [id, lv] of levels) {
    if (!byLevel.has(lv)) byLevel.set(lv, []);
    byLevel.get(lv).push(id);
  }
  const sortedLevels = [...byLevel.keys()].sort((a, b) => a - b);

  let maxRowW = 0;
  for (const lv of sortedLevels) {
    const n = byLevel.get(lv).length;
    maxRowW = Math.max(maxRowW, n * DAG_NODE_W + (n - 1) * DAG_H_GAP);
  }

  const positions = new Map();
  for (const lv of sortedLevels) {
    const ids  = byLevel.get(lv);
    const rowW = ids.length * DAG_NODE_W + (ids.length - 1) * DAG_H_GAP;
    const startX = (maxRowW - rowW) / 2 + DAG_PAD;
    ids.forEach((id, i) => {
      positions.set(id, {
        x: startX + i * (DAG_NODE_W + DAG_H_GAP),
        y: lv * (DAG_NODE_H + DAG_V_GAP) + DAG_PAD,
      });
    });
  }

  const maxLv = sortedLevels.length ? Math.max(...sortedLevels) : 0;
  return {
    positions,
    canvasW: maxRowW + DAG_PAD * 2,
    canvasH: (maxLv + 1) * (DAG_NODE_H + DAG_V_GAP) + DAG_PAD * 2,
  };
}

// ══════════════════════════════════════════════════════════════
//  DAG 渲染
// ══════════════════════════════════════════════════════════════
function renderDag(pipeline, tasks) {
  const area = document.getElementById('dag-area');
  if (!area) return;

  if (!tasks.length) {
    area.innerHTML = `<div class="dag-empty">
      <div style="font-size:36px;opacity:.25;margin-bottom:12px">◈</div>
      <div style="color:var(--text-2);margin-bottom:16px;font-size:14px">工作流暂无任务</div>
      <button class="btn primary" onclick="showAddTaskModal('${esc(pipeline.id)}')">+ 添加第一个任务</button>
    </div>`;
    return;
  }

  const { positions, canvasW, canvasH } = _computeDagLayout(tasks);
  const taskMap = new Map(tasks.map(t => [t.id, t]));
  const W = Math.max(canvasW, 480);
  const H = Math.max(canvasH, 320);

  // 收集边
  const edges = [];
  for (const t of tasks) {
    const deps = [...(t.depends_on || [])];
    if (t.parent_task_id && taskMap.has(t.parent_task_id) && !deps.includes(t.parent_task_id))
      deps.push(t.parent_task_id);
    for (const depId of deps) {
      if (positions.has(depId) && positions.has(t.id))
        edges.push({ from: depId, to: t.id });
    }
  }

  // SVG 边：用 style= 确保 CSS 变量解析（presentation attr 不支持 CSS var）
  const svgPaths = edges.map(({ from, to }) => {
    const fp = positions.get(from), tp = positions.get(to);
    const fx = fp.x + DAG_NODE_W / 2, fy = fp.y + DAG_NODE_H;
    const tx = tp.x + DAG_NODE_W / 2, ty = tp.y;
    const mid = (fy + ty) / 2;
    return `<path class="dag-edge" d="M${fx},${fy} C${fx},${mid} ${tx},${mid} ${tx},${ty}"/>`;
  }).join('');

  // 节点 HTML
  const nodesHtml = tasks.map(t => {
    const pos = positions.get(t.id);
    if (!pos) return '';
    const nodeRun = (pipeline.node_runs || []).find(n => n.task_id === t.id);
    const isSelected = workflowSelectedTaskId === t.id;
    const prompt = t.prompt.length > 70 ? t.prompt.slice(0, 68) + '…' : t.prompt;
    const nodeTitle = nodeRun?.node_name || prompt;
    const nodeSubtitle = nodeRun?.node_name ? prompt : '';
    const projectLabel = nodeRun?.resolved_project || t.project_name || t.project.split('/').pop();
    const dur = t.finished
      ? fmtDuration(t.created, t.finished)
      : t.status === 'running' ? '运行中' : statusLabel(t.status);

    return `<div class="dag-node dag-status-${t.status}${isSelected ? ' dag-selected' : ''}"
        id="dag-node-${esc(t.id)}"
        style="left:${pos.x}px;top:${pos.y}px;width:${DAG_NODE_W}px"
        onclick="selectDagNode('${esc(t.id)}')">
      <div class="dag-node-header">
        <span class="dag-node-status-dot"></span>
        <span style="font-size:10px;color:var(--text-3);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(t.account)}</span>
        <span class="badge ${t.type}" style="font-size:10px;flex-shrink:0">${t.type}</span>
      </div>
      <div class="dag-node-prompt">${esc(nodeTitle)}</div>
      ${nodeSubtitle ? `<div style="font-size:10px;color:var(--text-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:-2px">${esc(nodeSubtitle)}</div>` : ''}
      <div class="dag-node-footer">
        <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:55%">${esc(projectLabel)}</span>
        <span style="flex-shrink:0">${dur}</span>
      </div>
    </div>`;
  }).join('');

  area.innerHTML = `
    <div class="dag-canvas" style="width:${W}px;height:${H}px">
      <svg class="dag-svg" width="${W}" height="${H}" xmlns="http://www.w3.org/2000/svg">${svgPaths}</svg>
      ${nodesHtml}
    </div>`;
  // 恢复当前缩放级别
  if (dagZoom !== 1.0) {
    const canvas = area.querySelector('.dag-canvas');
    if (canvas) canvas.style.zoom = dagZoom;
  }
  const label = document.getElementById('dag-zoom-label');
  if (label) label.textContent = Math.round(dagZoom * 100) + '%';
}

// ══════════════════════════════════════════════════════════════
//  DAG 差量更新（避免全量重渲染破坏选中状态）
// ══════════════════════════════════════════════════════════════
function _patchDagNode(task, pipeline) {
  const nodeEl = document.getElementById(`dag-node-${task.id}`);
  if (!nodeEl) return false;
  const isSelected = workflowSelectedTaskId === task.id;
  const newClass = `dag-node dag-status-${task.status}${isSelected ? ' dag-selected' : ''}`;
  if (nodeEl.className !== newClass) nodeEl.className = newClass;
  // 更新 footer 时长/状态文本
  const durEl = nodeEl.querySelector('.dag-node-footer span:last-child');
  if (durEl) {
    durEl.textContent = task.finished
      ? fmtDuration(task.created, task.finished)
      : task.status === 'running' ? '运行中' : statusLabel(task.status);
  }
  return true;
}

function _patchDagIfRendered(pipeline, tasks) {
  const area = document.getElementById('dag-area');
  const existingNodes = area ? area.querySelectorAll('.dag-node') : [];
  const pTasks = tasks.filter(t => pipeline.task_ids.includes(t.id));
  // 节点数量变化（如重试后新增节点）或尚未渲染时，走全量渲染
  if (!area || existingNodes.length === 0 || existingNodes.length !== pTasks.length) {
    _showToolbar(pipeline);
    _renderActivePipeline(pipeline, tasks);
    return;
  }
  // 仅更新状态和时长
  pTasks.forEach(t => _patchDagNode(t, pipeline));
}

// ══════════════════════════════════════════════════════════════
//  DAG 缩放
// ══════════════════════════════════════════════════════════════
function zoomDag(delta) {
  dagZoom = delta === 0 ? 1.0 : Math.min(2.0, Math.max(0.3, dagZoom + delta));
  const canvas = document.querySelector('#dag-area .dag-canvas');
  if (canvas) canvas.style.zoom = dagZoom;
  const label = document.getElementById('dag-zoom-label');
  if (label) label.textContent = Math.round(dagZoom * 100) + '%';
}

function _renderDagEmpty() {
  const area = document.getElementById('dag-area');
  if (area) area.innerHTML = `<div class="dag-empty">
    <div style="font-size:36px;opacity:.2;margin-bottom:12px">⬡</div>
    <div style="color:var(--text-3);font-size:13px">从左侧选择运行记录</div>
  </div>`;
}

// ══════════════════════════════════════════════════════════════
//  画布工具栏
// ══════════════════════════════════════════════════════════════
function _showToolbar(pipeline) {
  const tb   = document.getElementById('dag-toolbar');
  const name = document.getElementById('dag-pipeline-name');
  if (tb)   tb.style.display = '';
  if (name) name.textContent = pipeline.name;
}

function _hideToolbar() {
  const tb = document.getElementById('dag-toolbar');
  if (tb) tb.style.display = 'none';
}

async function confirmDeletePipeline() {
  if (!activePipelineId) return;
  const p = pipelinesCache.find(p => p.id === activePipelineId);
  if (!confirm(`确定要删除工作流「${p?.name || activePipelineId}」？任务本身不会被删除。`)) return;
  try {
    const r = await fetch(`${API}/api/pipelines/${activePipelineId}`, { method: 'DELETE' });
    if (!r.ok && r.status !== 204) throw new Error(r.statusText);
    activePipelineId = null;
    localStorage.removeItem('coderfleet.activePipelineId');
    _hideToolbar();
    closeWorkflowDetail();
    await loadWorkflows();
  } catch (e) { alert('删除失败：' + e.message); }
}

// ══════════════════════════════════════════════════════════════
//  节点选中 + 右侧详情面板
// ══════════════════════════════════════════════════════════════
async function selectDagNode(taskId) {
  workflowSelectedTaskId = taskId;
  document.querySelectorAll('.dag-node').forEach(n => n.classList.remove('dag-selected'));
  const node = document.getElementById(`dag-node-${taskId}`);
  if (node) node.classList.add('dag-selected');
  await _openWorkflowDetail(taskId);
}

async function _openWorkflowDetail(taskId) {
  const detail = document.getElementById('workflow-detail');
  if (!detail) return;
  detail.classList.add('open');
  detail.innerHTML = `<div style="padding:20px;color:var(--text-3);font-size:12px">加载中...</div>`;

  try {
    const [task, logText] = await Promise.all([
      fetch(`${API}/api/tasks/${taskId}`).then(r => r.json()),
      fetch(`${API}/api/tasks/${taskId}/logs`).then(r => r.text()).catch(() => ''),
    ]);
    const dur = fmtDuration(task.created, task.finished);
    const pipeline = pipelinesCache.find(p => p.id === activePipelineId);
    const nodeRun = pipeline?.node_runs?.find(n => n.task_id === taskId);

    const canRetry = task.status === 'failed' || task.status === 'killed';
    detail.innerHTML = `
      <div class="workflow-detail-header">
        <div style="flex:1;min-width:0">
          <div style="font-size:12px;font-weight:600;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;margin-bottom:6px">${esc(nodeRun?.node_name || task.prompt)}</div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;font-size:11px;align-items:center">
            <span class="status-dot ${task.status}">${statusLabel(task.status)}</span>
            <span class="badge ${task.type}">${task.type}</span>
            <span style="color:var(--text-3)">${esc(task.account)}</span>
            <span style="color:var(--text-3)">${dur}</span>
            ${canRetry ? `<button class="btn" style="font-size:11px;padding:2px 9px;margin-left:2px" onclick="retryWorkflowTask('${esc(task.id)}')">↩ 重试</button>` : ''}
          </div>
          ${nodeRun ? `<div class="workflow-detail-meta">
            执行项目: ${esc(nodeRun.resolved_project || task.project_name)} · 目标: ${esc(_templateNodeTargetLabel(nodeRun))}
          </div>` : ''}
          ${nodeRun?.actual_prompt ? `<div class="workflow-detail-meta" style="margin-top:4px">
            <span style="color:var(--text-3)">实际 Prompt：</span>
            <div style="font-size:11px;margin-top:3px;white-space:pre-wrap;word-break:break-all;max-height:72px;overflow:auto;background:color-mix(in srgb,var(--bg) 80%,transparent);border-radius:4px;padding:4px 6px">${esc(nodeRun.actual_prompt)}</div>
          </div>` : ''}
        </div>
        <button class="close-btn" onclick="closeWorkflowDetail()">✕</button>
      </div>
      <div style="flex:1;overflow:auto;min-height:0" id="workflow-log-content"></div>`;

    const logEl = document.getElementById('workflow-log-content');
    if (logEl) {
      const r = new ChatLogRenderer(logEl, false, true);
      r.render(logText, task.type);
    }
  } catch (e) {
    detail.innerHTML = `<div style="padding:16px;color:var(--red)">加载失败：${esc(e.message)}</div>`;
  }
}

async function retryWorkflowTask(taskId) {
  try {
    const r = await fetch(`${API}/api/tasks/${taskId}/retry`, { method: 'POST' });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
    closeWorkflowDetail();
    // 重试后节点数量可能变化（pipeline 中旧 task 被替换），全量刷新
    if (activePipelineId) await openPipeline(activePipelineId);
    else await loadWorkflows();
  } catch (e) {
    alert('重试失败：' + e.message);
  }
}

function closeWorkflowDetail() {
  workflowSelectedTaskId = null;
  document.querySelectorAll('.dag-node').forEach(n => n.classList.remove('dag-selected'));
  const d = document.getElementById('workflow-detail');
  if (d) { d.classList.remove('open'); d.innerHTML = ''; }
}

// ══════════════════════════════════════════════════════════════
//  统一「添加任务」弹窗
//  pipelineId       : 已知工作流 ID（从画布工具栏 / DAG 节点进入时）
//  options.prefillDep : 预选为依赖的任务 ID
//  options.fromLog    : true = 从日志模态框进入，需选择/创建工作流
// ══════════════════════════════════════════════════════════════
async function showAddTaskModal(pipelineId, options = {}) {
  const { prefillDep = null, fromLog = false } = options;
  const modal = document.getElementById('add-task-modal');
  if (!modal) return;

  // 重置表单
  document.getElementById('add-task-prompt').value     = '';
  document.getElementById('add-task-auto').checked      = true;
  document.getElementById('add-task-pipeline-id').value = pipelineId || '';
  document.getElementById('add-task-parent-id').value   = prefillDep || '';
  const btn = document.getElementById('add-task-submit-btn');
  btn.disabled = false; btn.textContent = '添加任务';

  // 标题
  document.getElementById('add-task-modal-title').textContent = fromLog ? '派生子任务' : '添加任务';
  const pipeName    = pipelineId ? (pipelinesCache.find(p => p.id === pipelineId)?.name || '') : '';
  const parentSlice = prefillDep ? _getTaskPromptSlice(prefillDep) : '';
  document.getElementById('add-task-modal-subtitle').textContent =
    fromLog && parentSlice ? `父任务: ${parentSlice}` : pipeName;

  // 工作流选择器（仅 fromLog 且无 pipelineId 时显示）
  const pipelineGroup = document.getElementById('add-task-pipeline-group');
  if (fromLog && !pipelineId) {
    pipelineGroup.style.display = '';
    const sel = document.getElementById('add-task-pipeline-sel');
    sel.innerHTML = `<option value="__new__">-- 新建工作流 --</option>` +
      pipelinesCache.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');
    sel.value = '__new__';
    document.getElementById('add-task-new-pipeline-name').value = '';
    document.getElementById('add-task-new-pipeline-name-group').style.display = '';
  } else {
    pipelineGroup.style.display = 'none';
  }

  // 依赖复选框：当前工作流内的所有任务
  const depsGroup = document.getElementById('add-task-deps-group');
  const depsList  = document.getElementById('add-task-deps-list');
  const effectivePid = pipelineId || (prefillDep
    ? (workflowTasksCache.find(t => t.id === prefillDep)?.pipeline_id || null)
    : null);
  const pipeline = effectivePid ? pipelinesCache.find(p => p.id === effectivePid) : null;
  const pTasks   = pipeline
    ? workflowTasksCache.filter(t => pipeline.task_ids.includes(t.id))
    : (prefillDep ? workflowTasksCache.filter(t => t.id === prefillDep) : []);

  if (pTasks.length > 0) {
    depsGroup.style.display = '';
    depsList.innerHTML = pTasks.map(t => {
      const checked = t.id === prefillDep ? 'checked' : '';
      const label   = t.prompt.length > 55 ? t.prompt.slice(0, 53) + '…' : t.prompt;
      return `<div class="dep-checkbox-row">
        <input type="checkbox" id="dep-cb-${esc(t.id)}" value="${esc(t.id)}" ${checked}>
        <label for="dep-cb-${esc(t.id)}">
          <span class="status-dot ${t.status}" style="font-size:10px"></span>${esc(label)}
        </label>
      </div>`;
    }).join('');
  } else {
    depsGroup.style.display = 'none';
  }

  // 项目下拉
  await _loadAddTaskProjects(prefillDep);

  modal.style.display = 'flex';
  setTimeout(() => document.getElementById('add-task-prompt').focus(), 50);
}

function closeAddTaskModal(e) {
  if (e && e.target !== document.getElementById('add-task-modal')) return;
  document.getElementById('add-task-modal').style.display = 'none';
}

function onAddTaskPipelineChange() {
  const sel = document.getElementById('add-task-pipeline-sel');
  const grp = document.getElementById('add-task-new-pipeline-name-group');
  if (grp) grp.style.display = sel.value === '__new__' ? '' : 'none';
}

async function _loadAddTaskProjects(parentTaskId) {
  const sel = document.getElementById('add-task-project');
  if (!sel) return;
  try {
    if (parentTaskId) {
      const [logical, accounts] = await Promise.all([
        fetch(`${API}/api/projects/logical`).then(r => r.json()),
        fetch(`${API}/api/accounts`).then(r => r.json()).catch(() => []),
      ]);
      const parent = workflowTasksCache.find(t => t.id === parentTaskId) || currentTaskData;
      const pPath  = parent?.project || '';
      const group  = logical.find(g => pPath === g.path || pPath.startsWith(g.path + '/'));
      const accMap = new Map(accounts.map(a => [a.name, a]));
      const projs  = group ? group.projects
        : projectsCache.map(p => ({ name: p.name, account: p.account, type: p.account }));
      sel.innerHTML = '<option value="">选择项目</option>' + projs.map(p => {
        const acc = accMap.get(p.account);
        return `<option value="${esc(p.name)}">${esc(p.name)} · ${esc(p.account)}${acc?.busy ? ' ⚠ 忙碌' : ''}</option>`;
      }).join('');
    } else {
      sel.innerHTML = '<option value="">选择项目</option>' +
        projectsCache.map(p => `<option value="${esc(p.name)}">${esc(p.name)} · ${esc(p.account)}</option>`).join('');
    }
  } catch { /* ignore */ }
}

async function submitAddTask() {
  let pipelineId     = document.getElementById('add-task-pipeline-id').value;
  const parentTaskId = document.getElementById('add-task-parent-id').value;
  const projectName  = document.getElementById('add-task-project').value;
  const prompt       = document.getElementById('add-task-prompt').value.trim();
  const auto         = document.getElementById('add-task-auto').checked;

  if (!prompt)      { alert('请填写任务描述'); return; }
  if (!projectName) { alert('请选择执行项目'); return; }

  // 收集选中的依赖
  const depends_on = [...document.querySelectorAll('#add-task-deps-list input[type=checkbox]:checked')]
    .map(cb => cb.value);

  const btn = document.getElementById('add-task-submit-btn');
  btn.disabled = true; btn.textContent = '提交中...';

  try {
    // 需要创建或绑定工作流时
    const pipelineGroup = document.getElementById('add-task-pipeline-group');
    if (pipelineGroup.style.display !== 'none') {
      const sel = document.getElementById('add-task-pipeline-sel');
      if (sel.value === '__new__') {
        const name = document.getElementById('add-task-new-pipeline-name').value.trim()
          || prompt.slice(0, 20) || '新工作流';
        const initIds = parentTaskId ? [parentTaskId] : [];
        const rp = await fetch(`${API}/api/pipelines`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, task_ids: initIds }),
        });
        const pd = await rp.json();
        if (!rp.ok) throw new Error(pd.detail || rp.statusText);
        pipelineId = pd.id;
      } else {
        pipelineId = sel.value;
        if (parentTaskId) {
          await fetch(`${API}/api/pipelines/${pipelineId}/tasks`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: parentTaskId }),
          });
        }
      }
    }

    const r = await fetch(`${API}/api/tasks`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt,
        project_name:   projectName,
        auto,
        pipeline_id:    pipelineId || null,
        depends_on,
        parent_task_id: parentTaskId || null,
      }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);

    closeAddTaskModal();
    if (typeof closeLogModal === 'function') closeLogModal();

    activePipelineId = pipelineId || data.pipeline_id || activePipelineId;
    if (activePipelineId) localStorage.setItem('coderfleet.activePipelineId', activePipelineId);

    showPage('workflows');
    await loadWorkflows();
  } catch (e) {
    alert('添加任务失败：' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = '添加任务';
  }
}

// ══════════════════════════════════════════════════════════════
//  从日志模态框派生子任务（入口）
// ══════════════════════════════════════════════════════════════
async function spawnSubtaskFromLog() {
  if (!currentTaskData) return;
  if (!pipelinesCache.length) {
    try { pipelinesCache = await fetch(`${API}/api/pipelines`).then(r => r.json()); } catch { /* */ }
  }
  if (!workflowTasksCache.length || !workflowTasksCache.find(t => t.id === currentTaskData.id)) {
    workflowTasksCache = [...workflowTasksCache.filter(t => t.id !== currentTaskData.id), currentTaskData];
  }
  await showAddTaskModal(
    currentTaskData.pipeline_id || null,
    { prefillDep: currentTaskData.id, fromLog: !currentTaskData.pipeline_id },
  );
}

// ══════════════════════════════════════════════════════════════
//  新建工作流弹窗
// ══════════════════════════════════════════════════════════════
function showNewPipelineModal() {
  const modal = document.getElementById('new-pipeline-modal');
  if (!modal) return;
  document.getElementById('new-pipeline-name').value = '';
  modal.style.display = 'flex';
  setTimeout(() => document.getElementById('new-pipeline-name').focus(), 50);
}

function closeNewPipelineModal(e) {
  if (e && e.target !== document.getElementById('new-pipeline-modal')) return;
  document.getElementById('new-pipeline-modal').style.display = 'none';
}

async function submitNewPipeline() {
  const name = document.getElementById('new-pipeline-name').value.trim();
  if (!name) { alert('请输入工作流名称'); return; }
  const btn = document.getElementById('new-pipeline-submit-btn');
  btn.disabled = true; btn.textContent = '创建中...';
  try {
    const r = await fetch(`${API}/api/pipelines`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
    closeNewPipelineModal();
    activePipelineId = data.id;
    localStorage.setItem('coderfleet.activePipelineId', data.id);
    await loadWorkflows();
    showAddTaskModal(data.id);
  } catch (e) {
    alert('创建失败：' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = '创建';
  }
}

// ══════════════════════════════════════════════════════════════
//  辅助
// ══════════════════════════════════════════════════════════════
function _getTaskPromptSlice(taskId) {
  const t = workflowTasksCache.find(t => t.id === taskId) || currentTaskData;
  if (!t) return taskId.slice(0, 12);
  return t.prompt.slice(0, 45) + (t.prompt.length > 45 ? '…' : '');
}

// ══════════════════════════════════════════════════════════════
//  Tab 切换
// ══════════════════════════════════════════════════════════════
function switchWfTab(tab) {
  wfActiveTab = tab;
  document.getElementById('wf-tab-runs').classList.toggle('active', tab === 'runs');
  document.getElementById('wf-tab-templates').classList.toggle('active', tab === 'templates');
  document.getElementById('wf-panel-runs').style.display = tab === 'runs' ? '' : 'none';
  document.getElementById('wf-panel-templates').style.display = tab === 'templates' ? '' : 'none';
  if (tab === 'templates') loadTemplates();
  else loadWorkflows();
}

// ══════════════════════════════════════════════════════════════
//  模板列表
// ══════════════════════════════════════════════════════════════
async function loadTemplates() {
  try {
    const [templates, projects] = await Promise.all([
      fetch(`${API}/api/workflow-templates`).then(r => r.json()),
      fetch(`${API}/api/projects`).then(r => r.json()).catch(() => projectsCache),
    ]);
    templatesCache = templates;
    projectsCache = projects;
    renderTemplateList(templates);

    // 用户正在编辑时只刷新侧边栏列表，不重建编辑器（避免覆盖未保存内容）
    if (templateDirty) return;

    if (activeTemplateId) {
      const t = templates.find(t => t.id === activeTemplateId);
      if (t) _renderTemplateEditor(t);
      else { activeTemplateId = null; _clearTemplateEditor(); }
    } else {
      _clearTemplateEditor();
    }
  } catch (e) {
    console.error('loadTemplates:', e);
  }
}

function renderTemplateList(templates) {
  const list = document.getElementById('template-list');
  if (!list) return;
  if (!templates.length) {
    list.innerHTML = `<div class="empty" style="padding:20px 14px;font-size:13px">暂无模板<br><span style="color:var(--text-3);font-size:12px">点击右上角「+ 新建」</span></div>`;
    return;
  }
  list.innerHTML = templates.map(t => {
    const isActive = activeTemplateId === t.id;
    return `<div class="pipeline-list-item${isActive ? ' active' : ''}" onclick="openTemplate('${esc(t.id)}')">
      <div style="font-size:13px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(t.name)}</div>
      <div style="font-size:11px;color:var(--text-3);margin-top:2px">${t.nodes.length} 个节点 · ${fmtTime(t.updated || t.created)}</div>
    </div>`;
  }).join('');
}

async function newTemplate() {
  try {
    const r = await fetch(`${API}/api/workflow-templates`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: '新模板', description: '', nodes: [] }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
    activeTemplateId = data.id;
    templatesCache = [data, ...templatesCache];
    renderTemplateList(templatesCache);
    _renderTemplateEditor(data);
  } catch (e) {
    alert('创建失败：' + e.message);
  }
}

async function openTemplate(id) {
  activeTemplateId = id;
  renderTemplateList(templatesCache);
  try {
    const [tpl, projects] = await Promise.all([
      fetch(`${API}/api/workflow-templates/${id}`).then(r => r.json()),
      fetch(`${API}/api/projects`).then(r => r.json()).catch(() => projectsCache),
    ]);
    projectsCache = projects;
    _renderTemplateEditor(tpl);
  } catch (e) {
    alert('加载模板失败：' + e.message);
  }
}

// ══════════════════════════════════════════════════════════════
//  Drawflow 画布编辑器
// ══════════════════════════════════════════════════════════════
let dfEditor = null;
let suppressTemplateDirty = false;
let drawflowImportVersion = 0;

function _initDrawflow() {
  const mount = document.getElementById('drawflow-mount');
  if (!mount || dfEditor) return;

  const empty = document.getElementById('drawflow-empty');
  if (empty) empty.style.display = 'none';

  dfEditor = new Drawflow(mount);
  dfEditor.reroute = true;
  dfEditor.reroute_fix_curvature = true;
  dfEditor.start();

  dfEditor.on('nodeSelected',       id  => _openNodeDetail(id));
  dfEditor.on('nodeUnselected',     ()  => _closeNodeDetail());
  dfEditor.on('connectionCreated',  ()  => { if (!suppressTemplateDirty) markTemplateDirty(); });
  dfEditor.on('connectionRemoved',  ()  => { if (!suppressTemplateDirty) markTemplateDirty(); });
  dfEditor.on('nodeMoved',          ()  => markTemplateDirty());
  dfEditor.on('nodeRemoved',        ()  => { markTemplateDirty(); _closeNodeDetail(); });
}

function _clearTemplateEditor() {
  const tb   = document.getElementById('template-editor-toolbar');
  const empty = document.getElementById('drawflow-empty');
  if (tb) tb.style.display = 'none';
  if (dfEditor) {
    dfEditor.import({ drawflow: { Home: { data: {} } } });
    // Hide canvas, show empty state
    const pdf = document.querySelector('.parent-drawflow');
    if (pdf) pdf.style.display = 'none';
    if (empty) empty.style.display = '';
  }
  _closeNodeDetail();
}

function _renderTemplateEditor(tpl) {
  templateDirty = false;
  const tb = document.getElementById('template-editor-toolbar');
  if (!tb) return;

  tb.style.display = '';
  document.getElementById('tpl-name').value = tpl.name || '';
  _updateSaveBtn();

  _initDrawflow();
  _loadTemplateToCanvas(tpl);
  _closeNodeDetail();
}

// 将模板数据导入 Drawflow 画布
function _loadTemplateToCanvas(tpl) {
  if (!dfEditor) return;
  drawflowImportVersion += 1;
  const importVersion = drawflowImportVersion;
  const nodes = (tpl.nodes || []).map(_normalizeTemplateNodeData);

  // 展示画布，隐藏空状态
  const pdf   = document.querySelector('.parent-drawflow');
  const empty = document.getElementById('drawflow-empty');
  if (pdf)   pdf.style.display = '';
  if (empty) empty.style.display = 'none';

  if (!nodes.length) {
    dfEditor.import({ drawflow: { Home: { data: {} } } });
    _scheduleDrawflowConnectionRefresh([], importVersion);
    return;
  }

  // 为每个节点分配 Drawflow 数字 ID（按索引）
  const nodeToIdx = {};
  nodes.forEach((n, i) => { nodeToIdx[n.node_id] = i + 1; });

  const data = {};
  const connectionPairs = [];
  nodes.forEach((n, i) => {
    const dfId = i + 1;
    // 默认坐标：网格布局（无保存坐标时使用）
    const x = n.pos_x && n.pos_x !== 0 ? n.pos_x : 80 + (i % 3) * 320;
    const y = n.pos_y && n.pos_y !== 0 ? n.pos_y : 80 + Math.floor(i / 3) * 220;

    (n.depends_on || [])
      .filter(d => nodeToIdx[d])
      .forEach(d => connectionPairs.push({ from: String(nodeToIdx[d]), to: String(dfId) }));

    data[dfId] = {
      id: dfId,
      name: 'tpl-node',
      data: _normalizeTemplateNodeData(n),
      class: 'tpl-node',
      html: _buildDrawflowNodeHtml(n),
      typenode: false,
      inputs:  { input_1:  { connections: [] } },
      outputs: { output_1: { connections: [] } },
      pos_x: x,
      pos_y: y,
    };
  });

  dfEditor.import({ drawflow: { Home: { data } } });
  _scheduleDrawflowConnectionRefresh(connectionPairs, importVersion);
}

function _scheduleDrawflowConnectionRefresh(connectionPairs = [], importVersion = drawflowImportVersion) {
  if (!dfEditor) return;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      if (!dfEditor || importVersion !== drawflowImportVersion) return;
      suppressTemplateDirty = true;
      try {
        connectionPairs.forEach(({ from, to }) => {
          dfEditor.addConnection(from, to, 'output_1', 'input_1');
        });
      } finally {
        suppressTemplateDirty = false;
      }
      const data = dfEditor.export().drawflow.Home.data || {};
      Object.keys(data).forEach(id => dfEditor.updateConnectionNodes(`node-${id}`));
      templateDirty = false;
      _updateSaveBtn();
    });
  });
}

function _normalizeTemplateNodeData(data = {}) {
  const targetMode = data.target_mode || (data.project_role ? 'runtime_role' : 'default');
  return {
    node_id:      data.node_id || '',
    name:         data.name || '',
    prompt_tpl:   data.prompt_tpl || '',
    target_mode:  targetMode,
    project_name: data.project_name || '',
    project_role: data.project_role || '',
    depends_on:   data.depends_on || [],
    pos_x:        data.pos_x || 0,
    pos_y:        data.pos_y || 0,
  };
}

function _nextTemplateNodeId() {
  const used = new Set();
  if (dfEditor) {
    const exported = dfEditor.export().drawflow.Home.data || {};
    Object.values(exported).forEach(n => {
      const id = String(n.data?.node_id || '').trim();
      if (id) used.add(id);
    });
  }

  let next = Math.max(0, _nodeCounter) + 1;
  while (used.has(`node-${next}`)) next += 1;
  _nodeCounter = next;
  return `node-${next}`;
}

function _dedupeTemplateNodeIds(templateData) {
  const used = new Set();
  const remap = {};
  let maxNumericId = 0;

  const nodes = (templateData.nodes || []).map((node, index) => {
    const originalId = String(node.node_id || '').trim();
    const numeric = /^node-(\d+)$/.exec(originalId);
    if (numeric) maxNumericId = Math.max(maxNumericId, Number(numeric[1]));

    let nextId = originalId || `node-${index + 1}`;
    if (used.has(nextId)) {
      do {
        maxNumericId += 1;
        nextId = `node-${maxNumericId}`;
      } while (used.has(nextId));
    }

    used.add(nextId);
    if (originalId && !remap[originalId]) remap[originalId] = nextId;
    return { ...node, node_id: nextId };
  });

  return {
    ...templateData,
    nodes: nodes.map(node => ({
      ...node,
      depends_on: (node.depends_on || []).map(dep => remap[dep] || dep),
    })),
  };
}

function _templateNodeTargetLabel(data = {}) {
  const targetMode = data.target_mode || (data.project_role ? 'runtime_role' : 'default');
  if (targetMode === 'fixed_project') return data.project_name || '固定项目';
  if (targetMode === 'runtime_role') return data.project_role ? `角色: ${data.project_role}` : '运行时角色';
  return '默认项目';
}

function _projectOptions(selected = '', emptyLabel = '选择项目') {
  return `<option value="">${emptyLabel}</option>` +
    projectsCache.map(p => `<option value="${esc(p.name)}" ${p.name === selected ? 'selected' : ''}>${esc(p.name)} · ${esc(p.account)}</option>`).join('');
}

// 构建 Drawflow 节点的显示 HTML
function _buildDrawflowNodeHtml(data) {
  const normalized = _normalizeTemplateNodeData(data);
  const name   = normalized.name || '';
  const prompt = (normalized.prompt_tpl || '');
  const preview = prompt.length > 65 ? prompt.slice(0, 63) + '…' : prompt;

  const targetTag = `<span class="df-node-role">${esc(_templateNodeTargetLabel(normalized))}</span>`;
  const nameHtml = name
    ? esc(name)
    : `<span class="df-node-placeholder">节点名称</span>`;
  const promptHtml = preview
    ? `<div class="df-node-prompt">${esc(preview)}</div>`
    : `<div class="df-node-prompt df-node-placeholder">点击配置 Prompt…</div>`;

  return `<div class="df-tpl-node">
    <div class="df-node-title">${nameHtml}${targetTag}</div>
    ${promptHtml}
  </div>`;
}

// 从 Drawflow 画布提取模板数据
function _getTemplateFromCanvas() {
  const name = document.getElementById('tpl-name')?.value || '';
  // 保留已保存的 description，不在画布上编辑
  const tpl  = templatesCache.find(t => t.id === activeTemplateId);
  const description = tpl?.description || '';

  if (!dfEditor) return { name, description, nodes: [] };

  const exported = dfEditor.export().drawflow.Home.data;

  // dfId → node_id 映射
  const idMap = {};
  const usedNodeIds = new Set();
  let maxNumericId = 0;
  Object.entries(exported).forEach(([dfId, n]) => {
    const data = _normalizeTemplateNodeData(n.data);
    const originalId = String(data.node_id || '').trim();
    const numeric = /^node-(\d+)$/.exec(originalId);
    if (numeric) maxNumericId = Math.max(maxNumericId, Number(numeric[1]));

    let nodeId = originalId || `node-${dfId}`;
    if (usedNodeIds.has(nodeId)) {
      do {
        maxNumericId += 1;
        nodeId = `node-${maxNumericId}`;
      } while (usedNodeIds.has(nodeId));
    }

    usedNodeIds.add(nodeId);
    idMap[dfId] = nodeId;
  });

  const nodes = Object.entries(exported).map(([dfId, n]) => {
    const depends_on = Object.values(n.inputs || {})
      .flatMap(inp => inp.connections.map(c => idMap[c.node]).filter(Boolean));
    const data = _normalizeTemplateNodeData(n.data);
    return {
      node_id:      idMap[dfId],
      name:         data.name,
      prompt_tpl:   data.prompt_tpl,
      target_mode:  data.target_mode,
      project_name: data.project_name,
      project_role: data.project_role,
      depends_on,
      pos_x: n.pos_x || 0,
      pos_y: n.pos_y || 0,
    };
  });

  return { name, description, nodes };
}

// 添加新节点到画布
function addTemplateNode() {
  if (!dfEditor) return;
  const nodeId = _nextTemplateNodeId();
  const existing = Object.keys(dfEditor.export().drawflow.Home.data).length;
  const data = { node_id: nodeId, name: `节点 ${existing + 1}`, prompt_tpl: '', target_mode: 'default', project_name: '', project_role: '' };

  const mount = document.getElementById('drawflow-mount');
  const cx = mount ? mount.clientWidth  / 2 - 110 : 160;
  const cy = mount ? mount.clientHeight / 2 - 50  : 120;

  dfEditor.addNode('tpl-node', 1, 1, cx, cy, 'tpl-node', data, _buildDrawflowNodeHtml(data));
  markTemplateDirty();
}

// ── 节点属性面板 ─────────────────────────────────────────
function _openNodeDetail(dfId) {
  const node = dfEditor?.getNodeFromId(dfId);
  if (!node) return;
  const d = _normalizeTemplateNodeData(node.data);
  node.data = d;

  const panel = document.getElementById('tpl-node-detail');
  if (!panel) return;
  panel.classList.add('open');
  panel.innerHTML = `
    <div class="workflow-detail-header">
      <span style="font-size:13px;font-weight:600;flex:1">节点属性</span>
      <button class="close-btn" onclick="_closeNodeDetail()">✕</button>
    </div>
    <div class="tpl-detail-body">
      <div class="form-group">
        <label>节点名称</label>
        <input id="df-detail-name" value="${esc(d.name || '')}" placeholder="节点名称"
          oninput="_updateNodeField(${dfId}, 'name', this.value)">
      </div>
      <div class="form-group">
        <label>Prompt 模板
          <span style="color:var(--text-3);font-size:11px;font-weight:400">（{{input}} 替换为运行时输入）</span>
        </label>
        <textarea id="df-detail-prompt" rows="7" placeholder="描述此节点要完成的任务..."
          oninput="_updateNodeField(${dfId}, 'prompt_tpl', this.value)">${esc(d.prompt_tpl || '')}</textarea>
      </div>
      <div class="form-group">
        <label>执行目标</label>
        <select id="df-detail-target-mode" onchange="_updateNodeTargetMode(${dfId}, this.value)">
          <option value="default" ${d.target_mode === 'default' ? 'selected' : ''}>使用运行时默认项目</option>
          <option value="fixed_project" ${d.target_mode === 'fixed_project' ? 'selected' : ''}>固定项目</option>
          <option value="runtime_role" ${d.target_mode === 'runtime_role' ? 'selected' : ''}>运行时角色</option>
        </select>
      </div>
      <div class="form-group df-target-field" id="df-target-fixed" style="display:${d.target_mode === 'fixed_project' ? '' : 'none'}">
        <label>固定项目</label>
        <select id="df-detail-project" onchange="_updateNodeField(${dfId}, 'project_name', this.value)">
          ${_projectOptions(d.project_name)}
        </select>
      </div>
      <div class="form-group df-target-field" id="df-target-role" style="display:${d.target_mode === 'runtime_role' ? '' : 'none'}">
        <label>运行时角色
          <span style="color:var(--text-3);font-size:11px;font-weight:400">（运行模板时绑定到具体项目）</span>
        </label>
        <input id="df-detail-role" value="${esc(d.project_role || '')}" placeholder="例如：planner、implementer、reviewer"
          oninput="_updateNodeField(${dfId}, 'project_role', this.value)">
      </div>
      <div class="tpl-detail-hint">
        执行目标决定此节点由哪个项目账号运行；连线用于建立前置依赖。
      </div>
      <button class="btn danger" style="width:100%;margin-top:8px;font-size:12px"
        onclick="confirmRemoveNode(${dfId})">移除节点</button>
    </div>`;
}

function _closeNodeDetail() {
  const panel = document.getElementById('tpl-node-detail');
  if (panel) { panel.classList.remove('open'); panel.innerHTML = ''; }
}

function _updateNodeField(dfId, field, value) {
  if (!dfEditor) return;
  const node = dfEditor.getNodeFromId(dfId);
  if (!node) return;
  node.data[field] = value;
  dfEditor.updateNodeDataFromId(dfId, node.data);
  _refreshDrawflowNodeHtml(dfId, node.data);
  markTemplateDirty();
}

function _updateNodeTargetMode(dfId, value) {
  if (!dfEditor) return;
  const node = dfEditor.getNodeFromId(dfId);
  if (!node) return;
  node.data.target_mode = value;
  dfEditor.updateNodeDataFromId(dfId, node.data);
  const fixed = document.getElementById('df-target-fixed');
  const role = document.getElementById('df-target-role');
  if (fixed) fixed.style.display = value === 'fixed_project' ? '' : 'none';
  if (role) role.style.display = value === 'runtime_role' ? '' : 'none';
  _refreshDrawflowNodeHtml(dfId, node.data);
  markTemplateDirty();
}

function _refreshDrawflowNodeHtml(dfId, data) {
  const contentEl = document.querySelector(`#node-${dfId} .drawflow_content_node`);
  if (contentEl) contentEl.innerHTML = _buildDrawflowNodeHtml(data);
}

function confirmRemoveNode(dfId) {
  if (!confirm('确定移除此节点？相关连线也会一并删除。')) return;
  dfEditor?.removeNodeId(`node-${dfId}`);
  _closeNodeDetail();
  markTemplateDirty();
}

function markTemplateDirty() {
  templateDirty = true;
  _updateSaveBtn();
}

function _updateSaveBtn() {
  const btn = document.getElementById('tpl-save-btn');
  if (!btn) return;
  btn.textContent   = templateDirty ? '保存 *' : '保存';
  btn.style.opacity = templateDirty ? '1' : '.6';
}

async function saveTemplate() {
  if (!activeTemplateId) return;
  const data = _dedupeTemplateNodeIds(_getTemplateFromCanvas());
  if (!data.name.trim()) { alert('请填写模板名称'); return; }
  const btn = document.getElementById('tpl-save-btn');
  btn.disabled = true; btn.textContent = '保存中...';
  try {
    const r = await fetch(`${API}/api/workflow-templates/${activeTemplateId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const result = await r.json();
    if (!r.ok) throw new Error(result.detail || r.statusText);
    templateDirty = false;
    _updateSaveBtn();
    templatesCache = templatesCache.map(t => t.id === activeTemplateId ? result : t);
    renderTemplateList(templatesCache);
  } catch (e) {
    alert('保存失败：' + e.message);
  } finally {
    btn.disabled = false;
    if (!templateDirty) btn.textContent = '保存';
  }
}

async function confirmDeleteTemplate() {
  if (!activeTemplateId) return;
  const t = templatesCache.find(t => t.id === activeTemplateId);
  if (!confirm(`确定要删除模板「${t?.name || activeTemplateId}」？`)) return;
  const delId = activeTemplateId;
  try {
    await fetch(`${API}/api/workflow-templates/${delId}`, { method: 'DELETE' });
    activeTemplateId = null;
    templatesCache = templatesCache.filter(t => t.id !== delId);
    renderTemplateList(templatesCache);
    _clearTemplateEditor();
  } catch (e) {
    alert('删除失败：' + e.message);
  }
}

// ══════════════════════════════════════════════════════════════
//  运行模板弹窗
// ══════════════════════════════════════════════════════════════
async function showRunTemplateModal(templateId) {
  if (!templateId) { alert('请先选择模板'); return; }
  const modal = document.getElementById('run-template-modal');
  if (!modal) return;

  const tpl = templatesCache.find(t => t.id === templateId)
    || await fetch(`${API}/api/workflow-templates/${templateId}`).then(r => r.json()).catch(() => null);
  if (!tpl) { alert('找不到模板'); return; }
  if (!projectsCache.length) {
    try { projectsCache = await fetch(`${API}/api/projects`).then(r => r.json()); } catch { /* ignore */ }
  }
  const nodes = (tpl.nodes || []).map(_normalizeTemplateNodeData);
  const defaultNodes = nodes.filter(n => n.target_mode === 'default');
  const runtimeRoles = [...new Set(nodes
    .filter(n => n.target_mode === 'runtime_role')
    .map(n => n.project_role.trim())
    .filter(Boolean))];
  const unnamedRuntimeNodes = nodes.filter(n => n.target_mode === 'runtime_role' && !n.project_role.trim());

  document.getElementById('run-tpl-id').value   = templateId;
  document.getElementById('run-tpl-name').textContent = tpl.name;
  document.getElementById('run-tpl-input').value = '';
  const btn = document.getElementById('run-tpl-submit-btn');
  btn.disabled = false; btn.textContent = '▶ 开始运行';

  // Populate default project dropdown
  const defaultGroup = document.getElementById('run-tpl-default-project-group');
  if (defaultGroup) defaultGroup.style.display = defaultNodes.length ? '' : 'none';
  const defSel = document.getElementById('run-tpl-default-project');
  defSel.innerHTML = '<option value="">选择默认项目</option>' +
    projectsCache.map(p => `<option value="${esc(p.name)}">${esc(p.name)} · ${esc(p.account)}</option>`).join('');

  // Build role-map selects for each unique runtime role
  const roleGroup = document.getElementById('run-tpl-role-map-group');
  roleGroup.innerHTML = [
    unnamedRuntimeNodes.length
      ? `<div class="form-hint danger">有 ${unnamedRuntimeNodes.length} 个节点选择了“运行时角色”，但没有填写角色名。</div>`
      : '',
    runtimeRoles.map(role => `
    <div class="form-group">
      <label>角色「${esc(role)}」使用的项目 *</label>
      <select data-role="${esc(role)}">
        <option value="">选择项目</option>
        ${projectsCache.map(p => `<option value="${esc(p.name)}">${esc(p.name)} · ${esc(p.account)}</option>`).join('')}
      </select>
    </div>`).join('')
  ].join('');
  if (unnamedRuntimeNodes.length) {
    btn.disabled = true;
    btn.textContent = '请先补充角色名';
  }

  // 预填上次运行配置
  try {
    const saved = JSON.parse(localStorage.getItem(`coderfleet.runConfig.${templateId}`) || 'null');
    if (saved) {
      if (saved.defaultProject && defSel) defSel.value = saved.defaultProject;
      if (saved.projectMap) {
        document.querySelectorAll('#run-tpl-role-map-group select[data-role]').forEach(sel => {
          const v = saved.projectMap[sel.dataset.role];
          if (v) sel.value = v;
        });
      }
    }
  } catch { /* ignore */ }

  modal.style.display = 'flex';
  setTimeout(() => document.getElementById('run-tpl-input').focus(), 50);
}

function closeRunTemplateModal(e) {
  if (e && e.target !== document.getElementById('run-template-modal')) return;
  document.getElementById('run-template-modal').style.display = 'none';
}

async function submitRunTemplate() {
  const templateId     = document.getElementById('run-tpl-id').value;
  const input          = document.getElementById('run-tpl-input').value.trim();
  const defaultGroup   = document.getElementById('run-tpl-default-project-group');
  const needsDefault   = !defaultGroup || defaultGroup.style.display !== 'none';
  const defaultProject = document.getElementById('run-tpl-default-project').value;
  if (!input)          { alert('请填写输入内容'); return; }
  if (needsDefault && !defaultProject) { alert('请选择默认项目'); return; }

  const project_map = {};
  let missingRole = '';
  document.querySelectorAll('#run-tpl-role-map-group select[data-role]').forEach(sel => {
    if (sel.value) project_map[sel.dataset.role] = sel.value;
    else if (!missingRole) missingRole = sel.dataset.role;
  });
  if (missingRole) { alert(`请选择角色「${missingRole}」使用的项目`); return; }

  const btn = document.getElementById('run-tpl-submit-btn');
  btn.disabled = true; btn.textContent = '运行中...';
  try {
    const r = await fetch(`${API}/api/workflow-templates/${templateId}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input, default_project: defaultProject, project_map }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);

    // 保存本次运行配置，下次打开时预填
    const savedCfg = { defaultProject, projectMap: {} };
    document.querySelectorAll('#run-tpl-role-map-group select[data-role]').forEach(sel => {
      if (sel.value) savedCfg.projectMap[sel.dataset.role] = sel.value;
    });
    localStorage.setItem(`coderfleet.runConfig.${templateId}`, JSON.stringify(savedCfg));

    document.getElementById('run-template-modal').style.display = 'none';
    // Switch to runs tab and open the newly created pipeline
    activePipelineId = data.id;
    localStorage.setItem('coderfleet.activePipelineId', data.id);
    switchWfTab('runs');
  } catch (e) {
    alert('运行失败：' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = '▶ 开始运行';
  }
}
