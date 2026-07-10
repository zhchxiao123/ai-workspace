// ── 工作流 DAG 视图 ───────────────────────────────────────

const DAG_NODE_W  = 220;
const DAG_NODE_H  = 100;
const DAG_H_GAP   = 60;
const DAG_V_GAP   = 80;
const DAG_PAD     = 48;
let workflowAccountsCache = [];

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
      fetch(`${API}/api/workflow-runs`).then(r => r.json()),
      fetch(`${API}/api/tasks?limit=300`).then(r => r.json()).catch(() => []),
    ]);
    const templatePipelines = pipelines.filter(p => p.template_id);
    _notifyNewApprovals(templatePipelines);
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
      const st = p.status || 'running';
      if (wfRunsFilter === 'running') return st === 'running';
      if (wfRunsFilter === 'failed')  return st === 'failed' || st === 'partial';
      if (wfRunsFilter === 'done')    return st === 'succeeded';
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
    const pTasks = _workflowRunTasks(p, tasks);
    // 优先使用后端已计算的 status，兼容旧数据回退
    const status = p.status || (
      pTasks.some(t => t.status === 'running') ? 'running' :
      pTasks.every(t => t.status === 'done') && pTasks.length ? 'succeeded' :
      pTasks.some(t => t.status === 'failed' || t.status === 'killed') ? 'failed' : 'running'
    );
    const dot = status === 'running'   ? `<span class="status-dot running" style="font-size:10px">运行中</span>`
              : status === 'succeeded' ? `<span class="status-dot done"    style="font-size:10px">完成</span>`
              : status === 'failed'    ? `<span class="status-dot failed"  style="font-size:10px">失败</span>`
              : status === 'partial'   ? `<span class="status-dot failed"  style="font-size:10px">部分完成</span>`
              : `<span style="color:var(--text-3);font-size:10px">待执行</span>`;

    const isActive = activePipelineId === p.id;
    const errorHint = p.last_error
      ? `<div style="font-size:10px;color:var(--red,#d44);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(p.last_error)}">${esc(p.last_error)}</div>`
      : '';
    return `<div class="pipeline-list-item${isActive ? ' active' : ''}" onclick="openPipeline('${esc(p.id)}')">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:6px">
        <span style="font-size:13px;font-weight:500;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.name)}</span>
        ${dot}
      </div>
      <div style="font-size:11px;color:var(--text-3);margin-top:2px">${pTasks.length} 个任务 · ${fmtTime(p.updated || p.created)}</div>
      ${errorHint}
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
      fetch(`${API}/api/workflow-runs/${id}`).then(r => r.json()),
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
  const pTasks = _workflowRunTasks(pipeline, allTasks);
  _showToolbar(pipeline, pTasks);
  renderDag(pipeline, pTasks);
}

function _workflowStateToTaskStatus(state) {
  return ({
    pending: 'pending',
    waiting_deps: 'scheduled',
    running: 'running',
    succeeded: 'done',
    failed: 'failed',
    skipped: 'killed',
    cancelled: 'killed',
    waiting_approval: 'running',
  })[state] || 'pending';
}

// 节点真实执行状态 → DAG 视觉类名 + 文案。
// 优先使用 WorkflowRun.node_executions[].state（八态状态机）；
// 无节点态的 legacy 运行回退到 task.status。
const WF_NODE_STATE_META = {
  // WorkflowNodeState（后端权威状态机）
  pending:          { cls: 'pending',          label: '待执行' },
  waiting_deps:     { cls: 'scheduled',        label: '等待依赖' },
  running:          { cls: 'running',          label: '运行中' },
  succeeded:        { cls: 'done',             label: '成功' },
  failed:           { cls: 'failed',           label: '失败' },
  skipped:          { cls: 'skipped',          label: '已跳过' },
  cancelled:        { cls: 'killed',           label: '已取消' },
  waiting_approval: { cls: 'waiting-approval', label: '待批准' },
  // legacy TaskStatus 回退
  scheduled:        { cls: 'scheduled',        label: '定时中' },
  done:             { cls: 'done',             label: '成功' },
  killed:           { cls: 'killed',           label: '已终止' },
};

// 取一个 DAG 节点（可能是真实 task，也可能是 workflow 合成节点）的权威状态键。
function _dagNodeState(t) {
  return t.workflow_node_state || t.status || 'pending';
}

function _dagStateMeta(state) {
  return WF_NODE_STATE_META[state] || { cls: (state || 'pending'), label: statusLabel(state) };
}

// 记录上一轮各 run 的状态，用于检测「新进入待审批」的跃迁并提示用户。
const _wfRunStatusSeen = new Map();

function _notifyNewApprovals(runs) {
  for (const r of runs) {
    const prev = _wfRunStatusSeen.get(r.id);
    if (r.status === 'waiting_approval' && prev !== 'waiting_approval') {
      _wfToast(`工作流「${r.name || r.id}」等待人工批准`, r.id);
    }
    _wfRunStatusSeen.set(r.id, r.status);
  }
}

// 轻量浮层提示（点击可跳转到对应 run）。
function _wfToast(message, runId) {
  let host = document.getElementById('wf-toast-host');
  if (!host) {
    host = document.createElement('div');
    host.id = 'wf-toast-host';
    document.body.appendChild(host);
  }
  const el = document.createElement('div');
  el.className = 'wf-toast';
  el.innerHTML = `<span>⏸ ${esc(message)}</span>`;
  el.onclick = () => { if (runId) openPipeline(runId); el.remove(); };
  host.appendChild(el);
  setTimeout(() => el.remove(), 8000);
}

// 特殊状态在节点上的醒目标记（待批准 / 已跳过）。
function _dagNodeFlag(state) {
  if (state === 'waiting_approval') return '<div class="dag-node-flag dag-flag-approval">⏸ 待批准</div>';
  if (state === 'skipped') return '<div class="dag-node-flag dag-flag-skipped">⤳ 已跳过</div>';
  return '';
}

function _workflowNodeDomId(nodeId) {
  return `node:${nodeId}`;
}

function _workflowRunTasks(run, allTasks) {
  const taskMap = new Map((allTasks || []).map(t => [t.id, t]));
  const nodes = run.node_executions || [];
  if (!nodes.length) return (allTasks || []).filter(t => (run.task_ids || []).includes(t.id));

  const idByNode = new Map(nodes.map(n => [n.node_id, n.task_id || _workflowNodeDomId(n.node_id)]));
  return nodes.map(n => {
    const realTask = n.task_id ? taskMap.get(n.task_id) : null;
    const status = _workflowStateToTaskStatus(n.state);
    const deps = (n.depends_on || []).map(dep => idByNode.get(dep)).filter(Boolean);
    if (realTask) {
      return {
        ...realTask,
        depends_on: deps.length ? deps : (realTask.depends_on || []),
        workflow_node_id: n.node_id,
        workflow_node_state: n.state,
      };
    }
    return {
      id: _workflowNodeDomId(n.node_id),
      status,
      account: '等待调度',
      type: '',
      prompt: n.actual_prompt || n.name || n.node_id,
      project: n.resolved_project || n.project_name || '',
      project_name: n.resolved_project || n.project_name || '',
      created: run.created,
      finished: n.finished_at || null,
      depends_on: deps,
      workflow_node_id: n.node_id,
      workflow_node_state: n.state,
      workflow_placeholder: true,
    };
  });
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
    const status = pipeline.status || 'running';
    let icon = '◈';
    let mainText = '';
    let subHtml = '';

    if (pipeline.last_error) {
      // 有明确错误信息
      icon = '⚠';
      mainText = '执行遇到错误';
      subHtml = `<div style="margin-top:10px;max-width:440px;background:var(--bg-2);border:1px solid color-mix(in srgb,var(--red,#d44) 30%,transparent);border-radius:8px;padding:10px 14px;text-align:left">
        <div style="font-size:11px;font-weight:600;color:var(--red,#d44);margin-bottom:6px">错误详情</div>
        <pre style="font-size:11px;color:var(--text-2);white-space:pre-wrap;word-break:break-all;margin:0">${esc(pipeline.last_error)}</pre>
      </div>`;
    } else if (pipeline.template_id) {
      // 模板运行：任务由后台自动创建，不需要"添加任务"入口
      if (status === 'running') {
        icon = '⏳';
        mainText = '节点正在排队执行中...';
        subHtml = `<div style="font-size:12px;color:var(--text-3);margin-top:4px">后台正在按模板节点顺序自动提交任务</div>`;
      } else if (status === 'failed') {
        icon = '✕';
        mainText = '执行失败，无任务产出';
        subHtml = `<div style="font-size:12px;color:var(--text-3);margin-top:4px">可查看服务器日志了解详情，或点击工具栏「恢复运行」重试</div>`;
      } else {
        mainText = '暂无任务记录';
      }
    } else {
      // v1 手动流水线已下线：不再提供手动添加任务入口，引导改用模板
      mainText = '此工作流暂无任务记录';
      subHtml = `<div style="font-size:12px;color:var(--text-3);margin-top:4px">手动流水线已下线，请在「模板库」创建工作流模板后运行</div>`;
    }

    area.innerHTML = `<div class="dag-empty">
      <div style="font-size:32px;opacity:.3;margin-bottom:12px">${icon}</div>
      <div style="color:var(--text-2);font-size:14px">${mainText}</div>
      ${subHtml}
    </div>`;
    return;
  }

  const { positions, canvasW, canvasH } = _computeDagLayout(tasks);
  const taskMap = new Map(tasks.map(t => [t.id, t]));
  const stateById = new Map(tasks.map(t => [t.id, _dagNodeState(t)]));
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
    // 通向被跳过节点的边：虚线弱化，直观呈现未走的分支
    const skipped = stateById.get(to) === 'skipped';
    const cls = skipped ? 'dag-edge dag-edge-skipped' : 'dag-edge';
    return `<path class="${cls}" d="M${fx},${fy} C${fx},${mid} ${tx},${mid} ${tx},${ty}"/>`;
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
    const state = _dagNodeState(t);
    const meta = _dagStateMeta(state);
    const dur = (t.finished && state !== 'skipped')
      ? fmtDuration(t.created, t.finished)
      : state === 'running' ? '运行中' : meta.label;
    return `<div class="dag-node dag-status-${meta.cls}${isSelected ? ' dag-selected' : ''}"
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
      <div class="dag-node-flags">${_dagNodeFlag(state)}</div>
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
  const state = _dagNodeState(task);
  const meta = _dagStateMeta(state);
  const newClass = `dag-node dag-status-${meta.cls}${isSelected ? ' dag-selected' : ''}`;
  if (nodeEl.className !== newClass) nodeEl.className = newClass;
  // 同步特殊状态标记（待批准 / 已跳过）
  const flagsEl = nodeEl.querySelector('.dag-node-flags');
  if (flagsEl) {
    const flag = _dagNodeFlag(state);
    if (flagsEl.innerHTML !== flag) flagsEl.innerHTML = flag;
  }
  // 更新 footer 时长/状态文本
  const durEl = nodeEl.querySelector('.dag-node-footer span:last-child');
  if (durEl) {
    durEl.textContent = (task.finished && state !== 'skipped')
      ? fmtDuration(task.created, task.finished)
      : state === 'running' ? '运行中' : meta.label;
  }
  return true;
}

function _patchDagIfRendered(pipeline, tasks) {
  const area = document.getElementById('dag-area');
  const existingNodes = area ? area.querySelectorAll('.dag-node') : [];
  const pTasks = _workflowRunTasks(pipeline, tasks);
  // 节点数量变化（如重试后新增节点）或尚未渲染时，走全量渲染
  if (!area || existingNodes.length === 0 || existingNodes.length !== pTasks.length) {
    _showToolbar(pipeline, pTasks);
    _renderActivePipeline(pipeline, tasks);
    return;
  }
  // 仅更新状态和时长
  pTasks.forEach(t => _patchDagNode(t, pipeline));
  _updateToolbarStatus(pipeline, pTasks);
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
function _showToolbar(pipeline, tasks) {
  const tb   = document.getElementById('dag-toolbar');
  const name = document.getElementById('dag-pipeline-name');
  if (tb)   tb.style.display = '';
  if (name) name.textContent = pipeline.name;
  _updateToolbarStatus(pipeline, tasks || []);
}

function _updateToolbarStatus(pipeline, tasks) {
  const badge      = document.getElementById('dag-status-badge');
  const progress   = document.getElementById('dag-progress-label');
  const resumeBtn  = document.getElementById('dag-resume-btn');
  const cancelBtn  = document.getElementById('dag-cancel-btn');
  const approveBtn = document.getElementById('dag-approve-btn');
  if (!badge) return;

  // 计算进度：以模板节点总数 vs 已完成任务数
  const totalNodes = (pipeline.node_runs || []).length || tasks.length;
  const doneTasks  = tasks.filter(t => t.status === 'done').length;

  // 状态 badge
  const status = pipeline.status || (
    tasks.some(t => t.status === 'running') ? 'running' :
    tasks.every(t => t.status === 'done') && tasks.length ? 'succeeded' :
    tasks.some(t => t.status === 'failed' || t.status === 'killed') ? 'failed' : 'running'
  );

  const statusMap = {
    running:          { text: '运行中', cls: 'status-dot running' },
    waiting_approval: { text: '等待审批', cls: 'status-dot running' },
    succeeded:        { text: '已完成', cls: 'status-dot done' },
    failed:           { text: '失败',   cls: 'status-dot failed' },
    partial:          { text: '部分完成', cls: 'status-dot failed' },
    cancelled:        { text: '已取消', cls: 'status-dot killed' },
  };
  const s = statusMap[status] || statusMap.running;
  badge.className = s.cls;
  badge.textContent = s.text;
  badge.style.display = '';

  if (totalNodes > 0) {
    progress.textContent = `${doneTasks}/${totalNodes} 节点`;
    progress.style.display = '';
  } else {
    progress.style.display = 'none';
  }

  // Resume 按钮只在 failed / partial 且有源模板时显示
  const canResume = (status === 'failed' || status === 'partial') && pipeline.template_id;
  if (resumeBtn) resumeBtn.style.display = canResume ? '' : 'none';

  // 取消按钮只在 running / waiting_approval 时显示
  const canCancel = (status === 'running' || status === 'waiting_approval');
  if (cancelBtn) cancelBtn.style.display = canCancel ? '' : 'none';

  // 批准按钮只在 waiting_approval 时显示
  const canApprove = status === 'waiting_approval';
  if (approveBtn) approveBtn.style.display = canApprove ? '' : 'none';
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
    const r = await fetch(`${API}/api/workflow-runs/${activePipelineId}`, { method: 'DELETE' });
    if (!r.ok && r.status !== 204) throw new Error(r.statusText);
    activePipelineId = null;
    localStorage.removeItem('coderfleet.activePipelineId');
    _hideToolbar();
    closeWorkflowDetail();
    await loadWorkflows();
  } catch (e) { alert('删除失败：' + e.message); }
}

async function resumePipeline() {
  if (!activePipelineId) return;
  const btn = document.getElementById('dag-resume-btn');
  if (btn) { btn.disabled = true; btn.textContent = '恢复中...'; }
  try {
    const r = await fetch(`${API}/api/workflow-runs/${activePipelineId}/resume`, { method: 'POST' });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
    closeWorkflowDetail();
    await openPipeline(activePipelineId);
  } catch (e) {
    alert('恢复失败：' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '▶ 恢复运行'; }
  }
}

async function approveWorkflowRun() {
  if (!activePipelineId) return;
  const btn = document.getElementById('dag-approve-btn');
  if (btn) { btn.disabled = true; btn.textContent = '处理中...'; }
  try {
    // 先获取 run 拿到 approval_token
    const runR = await fetch(`${API}/api/workflow-runs/${activePipelineId}`);
    const run  = await runR.json();
    if (!runR.ok) throw new Error(run.detail || runR.statusText);
    const token = run.approval_token || '';
    const r = await fetch(`${API}/api/workflow-runs/${activePipelineId}/approve?token=${encodeURIComponent(token)}`, { method: 'POST' });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
    await loadWorkflows();
  } catch (e) {
    alert('批准失败：' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '✓ 批准继续'; }
  }
}

async function cancelWorkflowRun() {
  if (!activePipelineId) return;
  const p = pipelinesCache.find(p => p.id === activePipelineId);
  if (!confirm(`确定要取消工作流「${p?.name || activePipelineId}」？所有运行中的节点都会被终止。`)) return;
  const btn = document.getElementById('dag-cancel-btn');
  if (btn) { btn.disabled = true; btn.textContent = '取消中...'; }
  try {
    const r = await fetch(`${API}/api/workflow-runs/${activePipelineId}/cancel`, { method: 'POST' });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
    closeWorkflowDetail();
    await loadWorkflows();
  } catch (e) {
    alert('取消失败：' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '■ 取消'; }
  }
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
    const pipeline = pipelinesCache.find(p => p.id === activePipelineId);
    const nodeExec = pipeline?.node_executions?.find(n => n.task_id === taskId || _workflowNodeDomId(n.node_id) === taskId);
    if (nodeExec && !nodeExec.task_id) {
      detail.innerHTML = `
        <div class="workflow-detail-header">
          <div style="flex:1;min-width:0">
            <div style="font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-bottom:6px">${esc(nodeExec.name || nodeExec.node_id)}</div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;font-size:11px;align-items:center">
              <span class="status-dot ${_workflowStateToTaskStatus(nodeExec.state)}">${statusLabel(_workflowStateToTaskStatus(nodeExec.state))}</span>
              <span style="color:var(--text-3)">${esc(nodeExec.state)}</span>
            </div>
            <div class="workflow-detail-meta">
              执行项目: ${esc(nodeExec.resolved_project || nodeExec.project_name || '等待解析')} · 目标: ${esc(_templateNodeTargetLabel(nodeExec))}
            </div>
            ${nodeExec.actual_prompt ? `<div class="workflow-detail-meta" style="margin-top:4px">
              <span style="color:var(--text-3)">实际 Prompt：</span>
              <div style="font-size:11px;margin-top:3px;white-space:pre-wrap;word-break:break-all;max-height:120px;overflow:auto;background:color-mix(in srgb,var(--bg) 80%,transparent);border-radius:4px;padding:4px 6px">${esc(nodeExec.actual_prompt)}</div>
            </div>` : ''}
          </div>
          <button class="close-btn" onclick="closeWorkflowDetail()">✕</button>
        </div>
        ${nodeExec.state === 'waiting_approval' ? `
        <div style="padding:14px">
          <div style="font-size:12px;color:var(--yellow,#e0a53f);font-weight:600;margin-bottom:6px">⏸ 工作流已暂停于此审批节点，等待人工批准</div>
          <div style="font-size:11px;color:var(--text-3);margin-bottom:10px">批准后，下游节点将继续执行。</div>
          <button class="btn primary" onclick="approveWorkflowRun()">✓ 批准继续</button>
        </div>` : `
        <div style="padding:14px;font-size:12px;color:var(--text-3)">底层任务尚未创建。节点进入运行后会在这里显示日志和输出。</div>`}`;
      return;
    }

    const [task, logText, outputData] = await Promise.all([
      fetch(`${API}/api/tasks/${taskId}`).then(r => r.json()),
      fetch(`${API}/api/tasks/${taskId}/logs`).then(r => r.text()).catch(() => ''),
      fetch(`${API}/api/tasks/${taskId}/output`).then(r => r.json()).catch(() => ({ text: '' })),
    ]);
    const dur = fmtDuration(task.created, task.finished);
    const nodeRun = pipeline?.node_runs?.find(n => n.task_id === taskId);
    const nodeExecFull = pipeline?.node_executions?.find(n => n.task_id === taskId);
    // 节点输出优先取任务实时输出，回退到 WorkflowRun 保存的节点输出
    const outputText = outputData?.text || nodeExecFull?.outputs?.text || '';
    const attemptsHtml = _renderAttemptHistory(nodeExecFull);

    const canRetry = task.status === 'failed' || task.status === 'killed';
    const outputHtml = (task.status === 'done' && outputText) ? `
      <div class="workflow-output-section" id="workflow-output-section">
        <div class="workflow-output-header" onclick="_toggleOutputSection()">
          <span style="font-size:11px;font-weight:600;color:var(--text-2)">节点输出</span>
          <span id="workflow-output-toggle" style="font-size:10px;color:var(--text-3)">▲ 收起</span>
        </div>
        <div id="workflow-output-body" style="font-size:11px;white-space:pre-wrap;word-break:break-all;color:var(--text-1);max-height:160px;overflow:auto;padding:8px">
          ${esc(outputText.slice(0, 2000))}${outputText.length > 2000 ? '\n…（已截断）' : ''}
        </div>
        <div style="display:flex;gap:6px;padding:4px 8px 6px">
          <button class="btn" style="font-size:10px;padding:2px 8px" onclick="_copyOutput(${JSON.stringify(outputText)})">复制</button>
          ${outputText.length > 2000 ? `<span style="font-size:10px;color:var(--text-3);line-height:1.8">${outputText.length} 字符</span>` : ''}
        </div>
      </div>` : '';

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
      ${attemptsHtml}
      ${outputHtml}
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

// 渲染节点的重试历史（每次尝试的 task_id、状态、时间）。仅在有多次尝试时显示。
function _renderAttemptHistory(nodeExec) {
  const attempts = nodeExec?.attempts || [];
  if (attempts.length <= 1) return '';
  const rows = attempts.map((a, i) => {
    const st = a.state || 'unknown';
    const cls = ({ succeeded: 'done', failed: 'failed', killed: 'killed' })[st] || 'pending';
    const when = a.finished_at ? a.finished_at.replace('T', ' ') : '';
    return `<div style="display:flex;gap:8px;align-items:center;font-size:11px;padding:3px 0">
      <span style="color:var(--text-3);width:36px;flex-shrink:0">#${i + 1}</span>
      <span class="status-dot ${cls}">${statusLabel(cls)}</span>
      <span style="color:var(--text-3);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.task_id || '')}</span>
      <span style="color:var(--text-3);flex-shrink:0">${esc(when)}</span>
    </div>`;
  }).join('');
  return `<div class="workflow-output-section">
    <div class="workflow-output-header" style="cursor:default">
      <span style="font-size:11px;font-weight:600;color:var(--text-2)">重试历史（${attempts.length} 次尝试）</span>
    </div>
    <div style="padding:4px 8px 8px">${rows}</div>
  </div>`;
}

function _toggleOutputSection() {
  const body   = document.getElementById('workflow-output-body');
  const toggle = document.getElementById('workflow-output-toggle');
  if (!body) return;
  const collapsed = body.style.display === 'none';
  body.style.display = collapsed ? '' : 'none';
  if (toggle) toggle.textContent = collapsed ? '▲ 收起' : '▼ 展开';
}

function _copyOutput(text) {
  navigator.clipboard.writeText(text).catch(() => {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
  });
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

// v1 手动流水线的「添加任务」弹窗与派生子任务入口已下线（见 #17）。

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
    const [templates, projects, accounts] = await Promise.all([
      fetch(`${API}/api/workflow-templates`).then(r => r.json()),
      fetch(`${API}/api/projects`).then(r => r.json()).catch(() => projectsCache),
      fetch(`${API}/api/accounts`).then(r => r.json()).catch(() => workflowAccountsCache),
    ]);
    templatesCache = templates;
    projectsCache = projects;
    workflowAccountsCache = accounts;
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
    const [tpl, projects, accounts] = await Promise.all([
      fetch(`${API}/api/workflow-templates/${id}`).then(r => r.json()),
      fetch(`${API}/api/projects`).then(r => r.json()).catch(() => projectsCache),
      fetch(`${API}/api/accounts`).then(r => r.json()).catch(() => workflowAccountsCache),
    ]);
    projectsCache = projects;
    workflowAccountsCache = accounts;
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
    node_id:         data.node_id || '',
    name:            data.name || '',
    prompt_tpl:      data.prompt_tpl || '',
    target_mode:     targetMode,
    project_name:    data.project_name || '',
    project_role:    data.project_role || '',
    account:         data.account || '',
    execution_mode:  data.execution_mode || 'inherit',
    output_dir:      data.output_dir || '',
    ephemeral_retention: data.ephemeral_retention || 'release_on_finish',
    ephemeral_ttl_minutes: data.ephemeral_ttl_minutes ?? 120,
    depends_on:      data.depends_on || [],
    pos_x:           data.pos_x || 0,
    pos_y:           data.pos_y || 0,
    node_type:       data.node_type || 'task',
    timeout_minutes: data.timeout_minutes ?? 120,
    max_retries:     data.max_retries ?? 0,
    delay_seconds:   data.delay_seconds ?? 0,
    branches:        data.branches || [],
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

function _templateNodeCanRunWithoutProject(data = {}) {
  return (data.execution_mode || 'inherit') === 'ephemeral' && !!(data.account || '').trim();
}

function _projectOptions(selected = '', emptyLabel = '选择项目') {
  return `<option value="">${emptyLabel}</option>` +
    projectsCache.map(p => `<option value="${esc(p.name)}" ${p.name === selected ? 'selected' : ''}>${esc(p.name)} · ${esc(p.account)}</option>`).join('');
}

function _accountOptions(selected = '', emptyLabel = '跟随项目账号') {
  return `<option value="">${emptyLabel}</option>` +
    workflowAccountsCache.map(a => `<option value="${esc(a.name)}" ${a.name === selected ? 'selected' : ''}>${esc(a.name)} (${esc(a.type)})</option>`).join('');
}

// 构建 Drawflow 节点的显示 HTML
function _buildDrawflowNodeHtml(data) {
  const normalized = _normalizeTemplateNodeData(data);
  const name     = normalized.name || '';
  const nodeType = normalized.node_type || 'task';

  const nameHtml = name
    ? esc(name)
    : `<span class="df-node-placeholder">节点名称</span>`;

  const typeIcons = { task: '⚙', condition: '◇', delay: '⏱', approval: '✋' };
  const typeLabels = { task: '任务', condition: '条件', delay: '延迟', approval: '审批' };
  const typeIcon = typeIcons[nodeType] || '⚙';

  const typeTag  = nodeType !== 'task'
    ? `<span class="df-node-role df-node-type-${nodeType}">${typeIcon} ${typeLabels[nodeType] || nodeType}</span>`
    : `<span class="df-node-role">${esc(_templateNodeTargetLabel(normalized))}</span>`;

  let bodyHtml = '';
  if (nodeType === 'task') {
    const prompt  = normalized.prompt_tpl || '';
    const preview = prompt.length > 65 ? prompt.slice(0, 63) + '…' : prompt;
    bodyHtml = preview
      ? `<div class="df-node-prompt">${esc(preview)}</div>`
      : `<div class="df-node-prompt df-node-placeholder">点击配置 Prompt…</div>`;
  } else if (nodeType === 'condition') {
    const n = (normalized.branches || []).length;
    bodyHtml = `<div class="df-node-prompt">${n ? `${n} 个条件分支` : '点击配置分支…'}</div>`;
  } else if (nodeType === 'delay') {
    const sec = normalized.delay_seconds || 0;
    bodyHtml = `<div class="df-node-prompt">${sec > 0 ? `等待 ${sec}s` : '点击设置延迟…'}</div>`;
  } else if (nodeType === 'approval') {
    const prompt = normalized.prompt_tpl || '';
    bodyHtml = prompt
      ? `<div class="df-node-prompt">${esc(prompt.length > 65 ? prompt.slice(0, 63) + '…' : prompt)}</div>`
      : `<div class="df-node-prompt df-node-placeholder">点击配置审批说明…</div>`;
  }

  return `<div class="df-tpl-node df-tpl-node-${nodeType}">
    <div class="df-node-title">${nameHtml}${typeTag}</div>
    ${bodyHtml}
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
      node_id:         idMap[dfId],
      name:            data.name,
      prompt_tpl:      data.prompt_tpl,
      target_mode:     data.target_mode,
      project_name:    data.project_name,
      project_role:    data.project_role,
      account:         data.account,
      execution_mode:  data.execution_mode,
      output_dir:      data.output_dir,
      ephemeral_retention: data.ephemeral_retention,
      ephemeral_ttl_minutes: data.ephemeral_ttl_minutes,
      depends_on,
      pos_x:           n.pos_x || 0,
      pos_y:           n.pos_y || 0,
      node_type:       data.node_type || 'task',
      timeout_minutes: data.timeout_minutes ?? 120,
      max_retries:     data.max_retries ?? 0,
      delay_seconds:   data.delay_seconds ?? 0,
      branches:        data.branches || [],
    };
  });

  return { name, description, nodes };
}

// 添加新节点到画布（nodeType: 'task'|'condition'|'delay'|'approval'）
function addTemplateNode(nodeType = 'task') {
  if (!dfEditor) return;
  const nodeId = _nextTemplateNodeId();
  const existing = Object.keys(dfEditor.export().drawflow.Home.data).length;
  const typeNames = { task: '任务', condition: '条件', delay: '延迟', approval: '审批' };
  const data = {
    node_id: nodeId,
    name: `${typeNames[nodeType] || '节点'} ${existing + 1}`,
    prompt_tpl: '',
    target_mode: 'default',
    project_name: '',
    project_role: '',
    account: '',
    execution_mode: 'inherit',
    output_dir: '',
    ephemeral_retention: 'release_on_finish',
    ephemeral_ttl_minutes: 120,
    node_type: nodeType,
    timeout_minutes: nodeType === 'task' ? 120 : 0,
    max_retries: 0,
    delay_seconds: nodeType === 'delay' ? 60 : 0,
    branches: nodeType === 'condition' ? [{ condition: 'else', next_nodes: [] }] : [],
  };

  const mount = document.getElementById('drawflow-mount');
  const cx = mount ? mount.clientWidth  / 2 - 110 : 160;
  const cy = mount ? mount.clientHeight / 2 - 50  : 120;

  dfEditor.addNode('tpl-node', 1, 1, cx, cy, 'tpl-node', data, _buildDrawflowNodeHtml(data));
  markTemplateDirty();
}

// ── 节点属性面板 ─────────────────────────────────────────

// 从 Drawflow 当前连线中动态读取该节点的上游 node_id 列表
function _getNodeUpstreamIds(dfId) {
  if (!dfEditor) return [];
  const exported = dfEditor.export().drawflow.Home.data || {};
  const idMap = {};
  Object.entries(exported).forEach(([id, n]) => {
    idMap[id] = n.data?.node_id || `node-${id}`;
  });
  const node = exported[String(dfId)];
  if (!node) return [];
  return Object.values(node.inputs || {})
    .flatMap(inp => inp.connections.map(c => idMap[c.node]).filter(Boolean));
}

function _openNodeDetail(dfId) {
  const node = dfEditor?.getNodeFromId(dfId);
  if (!node) return;
  const d = _normalizeTemplateNodeData(node.data);
  node.data = d;

  const upstreamIds = _getNodeUpstreamIds(dfId);
  const availableVars = [
    { v: '{{input}}',  tip: '运行时输入' },
    ...upstreamIds.map(id => ({ v: `{{steps.${id}.outputs.text}}`, tip: `节点 ${id} 的输出` })),
  ];
  const varHintsHtml = `
    <div class="df-var-hints">
      <span class="df-var-hints-label">可插入变量</span>
      ${availableVars.map(({ v, tip }) =>
        `<code class="df-var-chip" title="${esc(tip)}"
          onclick="_insertVar(${dfId}, ${JSON.stringify(v)})">${esc(v)}</code>`
      ).join('')}
    </div>`;

  const nodeType = d.node_type || 'task';

  // ── 各节点类型专属区域 ──────────────────────────────────
  let typeSpecificHtml = '';

  if (nodeType === 'task') {
    typeSpecificHtml = `
      <div class="form-group">
        <label>Prompt 模板</label>
        <textarea id="df-detail-prompt" rows="7" placeholder="描述此节点要完成的任务..."
          oninput="_updateNodeField(${dfId}, 'prompt_tpl', this.value)">${esc(d.prompt_tpl || '')}</textarea>
        ${varHintsHtml}
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
      <div class="form-group">
        <label>账号
          <span style="color:var(--text-3);font-size:11px;font-weight:400">（临时沙箱未指定项目时使用）</span>
        </label>
        <select id="df-detail-account" onchange="_updateNodeField(${dfId}, 'account', this.value)">
          ${_accountOptions(d.account)}
        </select>
      </div>
      <div class="form-group">
        <label>执行环境</label>
        <select id="df-detail-execution-mode" onchange="_updateNodeField(${dfId}, 'execution_mode', this.value)">
          <option value="inherit" ${d.execution_mode === 'inherit' ? 'selected' : ''}>跟随项目配置</option>
          <option value="ephemeral" ${d.execution_mode === 'ephemeral' ? 'selected' : ''}>临时沙箱（执行后释放）</option>
          <option value="persistent" ${d.execution_mode === 'persistent' ? 'selected' : ''}>持久容器</option>
        </select>
      </div>
      <div class="form-group">
        <label>输出目录</label>
        <input id="df-detail-output-dir" value="${esc(d.output_dir || '')}" placeholder="留空使用默认输出目录"
          oninput="_updateNodeField(${dfId}, 'output_dir', this.value)">
      </div>
      <div class="form-group" style="display:flex;gap:8px">
        <div style="flex:1">
          <label>临时沙箱保留策略</label>
          <select id="df-detail-retention" onchange="_updateNodeField(${dfId}, 'ephemeral_retention', this.value)">
            <option value="release_on_finish" ${d.ephemeral_retention === 'release_on_finish' ? 'selected' : ''}>执行完释放</option>
            <option value="keep_until_ttl" ${d.ephemeral_retention === 'keep_until_ttl' ? 'selected' : ''}>保留到 TTL</option>
            <option value="keep_until_manual_close" ${d.ephemeral_retention === 'keep_until_manual_close' ? 'selected' : ''}>手动关闭前保留</option>
          </select>
        </div>
        <div style="width:96px">
          <label>TTL 分钟</label>
          <input type="number" min="1" max="1440" value="${d.ephemeral_ttl_minutes ?? 120}"
            oninput="_updateNodeField(${dfId}, 'ephemeral_ttl_minutes', +this.value)"
            style="width:100%">
        </div>
      </div>
      <div class="form-group" style="display:flex;gap:8px">
        <div style="flex:1">
          <label>超时（分钟，0=不限）</label>
          <input type="number" min="0" value="${d.timeout_minutes ?? 120}"
            oninput="_updateNodeField(${dfId}, 'timeout_minutes', +this.value)"
            style="width:100%">
        </div>
        <div style="flex:1">
          <label>失败重试次数</label>
          <input type="number" min="0" value="${d.max_retries ?? 0}"
            oninput="_updateNodeField(${dfId}, 'max_retries', +this.value)"
            style="width:100%">
        </div>
      </div>`;

  } else if (nodeType === 'condition') {
    typeSpecificHtml = `
      <div class="form-group">
        <label>条件分支
          <span style="color:var(--text-3);font-size:11px;font-weight:400">按顺序匹配，第一个命中的分支被激活</span>
        </label>
        <div id="df-branches-list">${_renderBranchesList(dfId, d.branches || [], availableVars)}</div>
        <button class="btn" style="width:100%;margin-top:6px;font-size:12px"
          onclick="_addConditionBranch(${dfId})">+ 添加分支</button>
      </div>
      <div class="tpl-detail-hint">
        条件格式：<code>{{steps.X.outputs.text}} contains '关键词'</code><br>
        支持：<code>contains</code> / <code>not_contains</code> / <code>equals</code> / <code>else</code><br>
        目标节点：填写要激活的下游节点 ID（逗号分隔）
      </div>`;

  } else if (nodeType === 'delay') {
    typeSpecificHtml = `
      <div class="form-group">
        <label>延迟时间（秒）</label>
        <input type="number" min="0" value="${d.delay_seconds ?? 0}"
          oninput="_updateNodeField(${dfId}, 'delay_seconds', +this.value)"
          style="width:100%">
      </div>
      <div class="tpl-detail-hint">工作流执行到此节点时暂停指定秒数后继续。</div>`;

  } else if (nodeType === 'approval') {
    typeSpecificHtml = `
      <div class="form-group">
        <label>审批说明</label>
        <textarea id="df-detail-prompt" rows="4" placeholder="请说明需要审批的内容和要求..."
          oninput="_updateNodeField(${dfId}, 'prompt_tpl', this.value)">${esc(d.prompt_tpl || '')}</textarea>
      </div>
      <div class="tpl-detail-hint">
        工作流执行到此节点时会暂停，等待在 DAG 视图中点击"✓ 批准继续"后才继续下一步。<br>
        审批令牌随运行详情一起返回，也可通过 API 批准。
      </div>`;
  }

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
        <label>节点类型</label>
        <select onchange="_updateNodeType(${dfId}, this.value)">
          <option value="task"      ${nodeType === 'task'      ? 'selected' : ''}>⚙ 任务（执行 Claude/Codex）</option>
          <option value="condition" ${nodeType === 'condition' ? 'selected' : ''}>◇ 条件（路由分支）</option>
          <option value="delay"     ${nodeType === 'delay'     ? 'selected' : ''}>⏱ 延迟（等待一段时间）</option>
          <option value="approval"  ${nodeType === 'approval'  ? 'selected' : ''}>✋ 审批（人工确认）</option>
        </select>
      </div>
      ${typeSpecificHtml}
      <button class="btn danger" style="width:100%;margin-top:8px;font-size:12px"
        onclick="confirmRemoveNode(${dfId})">移除节点</button>
    </div>`;
}

function _insertVar(dfId, variable) {
  const ta = document.getElementById('df-detail-prompt');
  if (!ta) return;
  const start = ta.selectionStart ?? ta.value.length;
  const end   = ta.selectionEnd   ?? ta.value.length;
  ta.value = ta.value.slice(0, start) + variable + ta.value.slice(end);
  ta.selectionStart = ta.selectionEnd = start + variable.length;
  ta.focus();
  _updateNodeField(dfId, 'prompt_tpl', ta.value);
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

function _updateNodeType(dfId, value) {
  if (!dfEditor) return;
  const node = dfEditor.getNodeFromId(dfId);
  if (!node) return;
  node.data.node_type = value;
  // seed sensible defaults when switching type
  if (value === 'condition' && !(node.data.branches || []).length) {
    node.data.branches = [{ condition: 'else', next_nodes: [] }];
  }
  if (value === 'delay' && !node.data.delay_seconds) {
    node.data.delay_seconds = 60;
  }
  dfEditor.updateNodeDataFromId(dfId, node.data);
  _refreshDrawflowNodeHtml(dfId, node.data);
  markTemplateDirty();
  // re-render detail panel to show type-specific fields
  _openNodeDetail(dfId);
}

// ── 条件分支编辑器 ─────────────────────────────────────────

function _renderBranchesList(dfId, branches, availableVars) {
  if (!branches.length) return '<div style="color:var(--text-3);font-size:12px;padding:4px 0">暂无分支</div>';
  const varOptions = availableVars.map(({ v }) => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
  return branches.map((b, i) => `
    <div class="branch-row" style="border:1px solid var(--border);border-radius:6px;padding:8px;margin-bottom:6px">
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
        <span style="font-size:11px;color:var(--text-3);min-width:30px">分支 ${i + 1}</span>
        <button class="btn" style="font-size:11px;padding:2px 6px;margin-left:auto"
          onclick="_removeBranch(${dfId}, ${i})">✕</button>
      </div>
      <div style="margin-bottom:4px">
        <label style="font-size:11px;color:var(--text-3)">条件表达式</label>
        <div style="display:flex;gap:4px">
          <input style="flex:1;font-size:12px" value="${esc(b.condition || '')}"
            placeholder="{{steps.X.outputs.text}} contains '关键词'  或  else"
            oninput="_updateBranchField(${dfId}, ${i}, 'condition', this.value)">
          <select style="font-size:11px;width:auto" onchange="_insertBranchVar(${dfId}, ${i}, this.value); this.value=''">
            <option value="">插入变量</option>
            ${varOptions}
          </select>
        </div>
      </div>
      <div>
        <label style="font-size:11px;color:var(--text-3)">激活节点（逗号分隔 node_id）</label>
        <input style="font-size:12px;width:100%" value="${esc((b.next_nodes || []).join(', '))}"
          placeholder="node-2, node-3"
          oninput="_updateBranchField(${dfId}, ${i}, 'next_nodes', this.value.split(',').map(s=>s.trim()).filter(Boolean))">
      </div>
    </div>`).join('');
}

function _addConditionBranch(dfId) {
  if (!dfEditor) return;
  const node = dfEditor.getNodeFromId(dfId);
  if (!node) return;
  node.data.branches = [...(node.data.branches || []), { condition: '', next_nodes: [] }];
  dfEditor.updateNodeDataFromId(dfId, node.data);
  _refreshDrawflowNodeHtml(dfId, node.data);
  markTemplateDirty();
  const list = document.getElementById('df-branches-list');
  const upstreamIds = _getNodeUpstreamIds(dfId);
  const availableVars = [
    { v: '{{input}}', tip: '运行时输入' },
    ...upstreamIds.map(id => ({ v: `{{steps.${id}.outputs.text}}`, tip: `节点 ${id} 的输出` })),
  ];
  if (list) list.innerHTML = _renderBranchesList(dfId, node.data.branches, availableVars);
}

function _removeBranch(dfId, index) {
  if (!dfEditor) return;
  const node = dfEditor.getNodeFromId(dfId);
  if (!node) return;
  node.data.branches = (node.data.branches || []).filter((_, i) => i !== index);
  dfEditor.updateNodeDataFromId(dfId, node.data);
  _refreshDrawflowNodeHtml(dfId, node.data);
  markTemplateDirty();
  const list = document.getElementById('df-branches-list');
  const upstreamIds = _getNodeUpstreamIds(dfId);
  const availableVars = [
    { v: '{{input}}', tip: '运行时输入' },
    ...upstreamIds.map(id => ({ v: `{{steps.${id}.outputs.text}}`, tip: `节点 ${id} 的输出` })),
  ];
  if (list) list.innerHTML = _renderBranchesList(dfId, node.data.branches, availableVars);
}

function _updateBranchField(dfId, index, field, value) {
  if (!dfEditor) return;
  const node = dfEditor.getNodeFromId(dfId);
  if (!node) return;
  const branches = [...(node.data.branches || [])];
  if (!branches[index]) return;
  branches[index] = { ...branches[index], [field]: value };
  node.data.branches = branches;
  dfEditor.updateNodeDataFromId(dfId, node.data);
  _refreshDrawflowNodeHtml(dfId, node.data);
  markTemplateDirty();
}

function _insertBranchVar(dfId, index, varExpr) {
  if (!varExpr) return;
  const node = dfEditor?.getNodeFromId(dfId);
  if (!node) return;
  const branches = [...(node.data.branches || [])];
  if (!branches[index]) return;
  const current = branches[index].condition || '';
  branches[index] = { ...branches[index], condition: current ? `${current} ${varExpr}` : varExpr };
  node.data.branches = branches;
  dfEditor.updateNodeDataFromId(dfId, node.data);
  markTemplateDirty();
  // refresh branch row input value
  const list = document.getElementById('df-branches-list');
  const upstreamIds = _getNodeUpstreamIds(dfId);
  const availableVars = [
    { v: '{{input}}', tip: '运行时输入' },
    ...upstreamIds.map(id => ({ v: `{{steps.${id}.outputs.text}}`, tip: `节点 ${id} 的输出` })),
  ];
  if (list) list.innerHTML = _renderBranchesList(dfId, node.data.branches, availableVars);
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
  if (!workflowAccountsCache.length) {
    try { workflowAccountsCache = await fetch(`${API}/api/accounts`).then(r => r.json()); } catch { /* ignore */ }
  }
  const nodes = (tpl.nodes || []).map(_normalizeTemplateNodeData);
  const defaultNodes = nodes.filter(n => n.target_mode === 'default' && !_templateNodeCanRunWithoutProject(n));
  const runtimeRoles = [...new Set(nodes
    .filter(n => n.target_mode === 'runtime_role' && !_templateNodeCanRunWithoutProject(n))
    .map(n => n.project_role.trim())
    .filter(Boolean))];
  const unnamedRuntimeNodes = nodes.filter(n => n.target_mode === 'runtime_role' && !n.project_role.trim() && !_templateNodeCanRunWithoutProject(n));

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
  const accountSel = document.getElementById('run-tpl-default-account');
  if (accountSel) {
    accountSel.innerHTML = '<option value="">选择账号</option>' +
      workflowAccountsCache.map(a => `<option value="${esc(a.name)}">${esc(a.name)} (${esc(a.type)})</option>`).join('');
  }

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
      const accountSel = document.getElementById('run-tpl-default-account');
      if (saved.defaultAccount && accountSel) accountSel.value = saved.defaultAccount;
      const policySel = document.getElementById('run-tpl-workspace-policy');
      if (saved.workspacePolicy && policySel) policySel.value = saved.workspacePolicy;
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
  const defaultAccount = document.getElementById('run-tpl-default-account')?.value || '';
  const workspacePolicy = document.getElementById('run-tpl-workspace-policy')?.value || 'isolated';
  if (!input)          { alert('请填写输入内容'); return; }
  if (needsDefault && !defaultProject && workspacePolicy !== 'shared_ephemeral') { alert('请选择默认项目'); return; }
  if (workspacePolicy === 'shared_ephemeral' && !defaultProject && !defaultAccount) { alert('共享临时沙箱未选择默认项目时，请选择默认账号'); return; }

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
      body: JSON.stringify({ input, default_project: defaultProject, default_account: defaultAccount, project_map, workspace_policy: workspacePolicy }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);

    // 保存本次运行配置，下次打开时预填
    const savedCfg = { defaultProject, defaultAccount, workspacePolicy, projectMap: {} };
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
