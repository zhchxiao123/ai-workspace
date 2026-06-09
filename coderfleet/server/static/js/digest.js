// ── 任务日报页 ────────────────────────────────────────────

let digestActiveDates = new Set();  // YYYY-MM-DD strings with task data
let digestSelectedDate  = null;
let digestCalYear       = new Date().getFullYear();
let digestCalMonth      = new Date().getMonth();   // 0-indexed
let digestAutoRefreshTimer = null;

// ── 初始化 ────────────────────────────────────────────────

async function initDigestPage() {
  renderDigestCalendar();   // show skeleton immediately
  await loadDigestDates();
}

// ── 数据加载 ──────────────────────────────────────────────

async function loadDigestDates() {
  try {
    const dates = await fetch(`${API}/api/digest/dates`).then(r => r.json());
    digestActiveDates = new Set(dates);

    // Navigate calendar to most recent active month
    if (dates.length) {
      const latest = dates[0];  // API returns newest-first
      digestCalYear  = parseInt(latest.slice(0, 4), 10);
      digestCalMonth = parseInt(latest.slice(5, 7), 10) - 1;
    }

    renderDigestCalendar();

    // Auto-select: today if it has data, otherwise most recent
    const today = new Date().toISOString().slice(0, 10);
    const pick = digestActiveDates.has(today) ? today : dates[0];
    if (pick) selectDigestDate(pick);
  } catch (e) {
    document.getElementById('digest-calendar').innerHTML =
      '<div class="empty" style="padding:20px 8px;font-size:12px">加载失败</div>';
  }
}

// ── 日历渲染 ──────────────────────────────────────────────

function renderDigestCalendar() {
  const wrap = document.getElementById('digest-calendar');
  if (!wrap) return;

  const today = new Date().toISOString().slice(0, 10);
  const year  = digestCalYear;
  const month = digestCalMonth;

  const firstDay    = new Date(year, month, 1);
  const lastDay     = new Date(year, month + 1, 0);
  const startOffset = (firstDay.getDay() + 6) % 7;   // Mon = 0

  const monthLabel = `${year}年${month + 1}月`;

  // Weekday headers
  const weekdays = ['一','二','三','四','五','六','日'].map(
    d => `<div class="cal-wday">${d}</div>`
  ).join('');

  // Day cells
  let cells = '';
  const totalCells = startOffset + lastDay.getDate();
  for (let i = 0; i < Math.ceil(totalCells / 7) * 7; i++) {
    const dayNum = i - startOffset + 1;
    if (dayNum < 1 || dayNum > lastDay.getDate()) {
      cells += '<div class="cal-day"></div>';
      continue;
    }
    const mm     = String(month + 1).padStart(2, '0');
    const dd     = String(dayNum).padStart(2, '0');
    const dateStr = `${year}-${mm}-${dd}`;

    const hasData  = digestActiveDates.has(dateStr);
    const isToday  = dateStr === today;
    const isSel    = dateStr === digestSelectedDate;

    const cls = ['cal-day',
      hasData  ? 'has-data'  : 'no-data',
      isToday  ? 'cal-today' : '',
      isSel    ? 'cal-sel'   : '',
    ].filter(Boolean).join(' ');

    const handler = hasData ? `onclick="selectDigestDate('${dateStr}')"` : '';
    cells += `<div class="${cls}" ${handler} title="${dateStr}">${dayNum}</div>`;
  }

  wrap.innerHTML = `
    <div class="cal-header">
      <button class="cal-nav-btn" onclick="digestCalNav(-1)" title="上个月">&#8249;</button>
      <span class="cal-month-label">${monthLabel}</span>
      <button class="cal-nav-btn" onclick="digestCalNav(1)"  title="下个月">&#8250;</button>
    </div>
    <div class="cal-weekdays">${weekdays}</div>
    <div class="cal-grid">${cells}</div>
    <div class="cal-legend">
      <span class="cal-legend-dot has-data"></span><span>有记录</span>
    </div>
  `;
}

function digestCalNav(delta) {
  digestCalMonth += delta;
  if (digestCalMonth < 0)  { digestCalMonth = 11; digestCalYear--; }
  if (digestCalMonth > 11) { digestCalMonth = 0;  digestCalYear++; }
  renderDigestCalendar();
}

// ── 日期选择 ──────────────────────────────────────────────

async function selectDigestDate(date) {
  digestSelectedDate = date;
  // Navigate calendar to the selected date's month
  const y = parseInt(date.slice(0, 4), 10);
  const m = parseInt(date.slice(5, 7), 10) - 1;
  if (digestCalYear !== y || digestCalMonth !== m) {
    digestCalYear  = y;
    digestCalMonth = m;
  }
  renderDigestCalendar();
  clearDigestAutoRefresh();
  await loadDigestDetail(date);
}

// ── 详情加载 ──────────────────────────────────────────────

async function loadDigestDetail(date) {
  const stickyEl = document.getElementById('digest-sticky-header');
  const bodyEl   = document.getElementById('digest-scroll-body');
  bodyEl.innerHTML = '<div class="empty" style="padding:40px 0">加载中...</div>';

  try {
    const digest = await fetch(`${API}/api/digest/${date}`).then(r => r.json());
    renderDigestStickyHeader(digest);
    renderDigestBody(digest);

    if (digest.status === 'generating' && digest.ai_task_id) {
      scheduleDigestAutoRefresh(date, digest.ai_task_id);
    }
  } catch (e) {
    stickyEl.style.display = 'none';
    bodyEl.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

// ── 固定头部（日期 + 统计） ───────────────────────────────

function renderDigestStickyHeader(digest) {
  const stickyEl = document.getElementById('digest-sticky-header');
  const total  = digest.total_done + digest.total_failed + digest.total_killed;

  const today     = new Date().toISOString().slice(0, 10);
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  const relLabel  = digest.date === today ? '今天' : digest.date === yesterday ? '昨天' : '';

  // 小屏日期导航：按有数据的日期前后翻页
  const sorted   = [...digestActiveDates].sort();
  const idx      = sorted.indexOf(digest.date);
  const prevDate = sorted[idx - 1] || '';
  const nextDate = sorted[idx + 1] || '';

  stickyEl.innerHTML = `
    <div class="digest-mobile-nav">
      <button class="digest-mobile-nav-btn" ${!prevDate ? 'disabled' : ''}
              onclick="selectDigestDate('${prevDate}')">‹</button>
      <div class="digest-mobile-nav-date">
        ${digest.date}${relLabel ? ' · ' + relLabel : ''}
      </div>
      <button class="digest-mobile-nav-btn" ${!nextDate ? 'disabled' : ''}
              onclick="selectDigestDate('${nextDate}')">›</button>
    </div>
    <div class="digest-header-row">
      <div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">
        <div class="digest-date-title">${digest.date}</div>
        ${relLabel ? `<span class="digest-rel-badge">${relLabel}</span>` : ''}
        <div class="digest-task-count">${total} 个任务</div>
      </div>
    </div>
    <div class="digest-stats-row">
      <div class="digest-stat-card">
        <div class="digest-stat-num done">${digest.total_done}</div>
        <div class="digest-stat-label">完成</div>
      </div>
      <div class="digest-stat-card">
        <div class="digest-stat-num failed">${digest.total_failed}</div>
        <div class="digest-stat-label">失败</div>
      </div>
      <div class="digest-stat-card ${digest.total_killed ? 'warn' : ''}">
        <div class="digest-stat-num">${digest.total_killed}</div>
        <div class="digest-stat-label">终止</div>
      </div>
    </div>
  `;
  stickyEl.style.display = '';
}

// ── 滚动正文（项目明细 + AI 摘要） ───────────────────────

function renderDigestBody(digest) {
  const bodyEl = document.getElementById('digest-scroll-body');
  const total  = digest.total_done + digest.total_failed + digest.total_killed;

  // Project breakdown
  let projectsHtml = '';
  if (digest.projects && digest.projects.length) {
    const rows = digest.projects.map(p => `
      <tr>
        <td style="font-weight:600;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
            title="${esc(p.project_name)}">${esc(p.project_name)}</td>
        <td><span class="digest-badge done">${p.done}</span></td>
        <td><span class="digest-badge failed">${p.failed || 0}</span></td>
        <td><span class="digest-badge muted">${p.killed || 0}</span></td>
      </tr>
    `).join('');
    projectsHtml = `
      <div class="digest-section">
        <div class="digest-section-title">项目明细</div>
        <table class="digest-table">
          <thead>
            <tr><th>项目</th><th>完成</th><th>失败</th><th>终止</th></tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  // AI summary section
  let aiHtml = '';
  if (digest.status === 'ready' && digest.ai_summary) {
    aiHtml = `
      <div class="digest-section">
        <div class="digest-section-header">
          <div class="digest-section-title">AI 日报摘要</div>
          <div class="digest-ai-meta">
            ${digest.generated_at ? '生成于 ' + fmtDatetime(digest.generated_at) : ''}
            <button class="btn" style="font-size:11px;margin-left:8px"
                    onclick="submitDigestGenerate('${digest.date}')">重新生成</button>
          </div>
        </div>
        <div class="digest-ai-content markdown-body">${marked.parse(digest.ai_summary)}</div>
      </div>
    `;
  } else if (digest.status === 'generating') {
    aiHtml = `
      <div class="digest-section">
        <div class="digest-section-title">AI 日报摘要</div>
        <div class="digest-ai-pending">
          <div class="digest-ai-spinner"></div>
          <span>正在生成摘要…</span>
          <button class="btn" style="font-size:12px"
                  onclick="openLogModal('${esc(digest.ai_task_id)}')">查看进度</button>
          <button class="btn" style="font-size:12px"
                  onclick="loadDigestDetail('${digest.date}')">刷新</button>
        </div>
      </div>
    `;
  } else if (digest.status === 'error') {
    aiHtml = `
      <div class="digest-section">
        <div class="digest-section-title">AI 日报摘要</div>
        <div class="digest-ai-error">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          摘要生成失败
          ${total > 0 ? `<button class="btn" style="font-size:12px;margin-left:8px"
                onclick="submitDigestGenerate('${digest.date}')">重试</button>` : ''}
        </div>
      </div>
    `;
  } else {
    aiHtml = `
      <div class="digest-section digest-ai-cta">
        <div class="digest-section-title">AI 日报摘要</div>
        ${total > 0 ? `
          <p class="digest-ai-hint">让 AI 分析今日工作，自动生成结构化日报</p>
          <button class="btn primary" id="digest-gen-btn"
                  onclick="submitDigestGenerate('${digest.date}')">
            生成日报摘要
          </button>
        ` : `
          <p class="digest-ai-hint">该日期暂无已完成的任务记录</p>
        `}
      </div>
    `;
  }

  bodyEl.innerHTML = projectsHtml + aiHtml;
  bodyEl.scrollTop = 0;
}

// ── 生成摘要 ──────────────────────────────────────────────

async function submitDigestGenerate(date) {
  const btn = document.getElementById('digest-gen-btn');
  if (btn) { btn.disabled = true; btn.textContent = '提交中…'; }
  try {
    const resp = await fetch(`${API}/api/digest/${date}/generate`, {
      method: 'POST',
    }).then(r => {
      if (!r.ok) return r.json().then(e => Promise.reject(new Error(e.detail || r.statusText)));
      return r.json();
    });
    if (resp.task_id) await loadDigestDetail(date);
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = '生成日报摘要'; }
    alert('提交失败：' + (e.message || '未知错误'));
  }
}

// ── 自动刷新（等待生成完成） ──────────────────────────────

function scheduleDigestAutoRefresh(date, taskId) {
  clearDigestAutoRefresh();
  digestAutoRefreshTimer = setTimeout(async () => {
    if (digestSelectedDate !== date) return;
    try {
      const task = await fetch(`${API}/api/tasks/${taskId}`).then(r => r.json());
      if (['done', 'failed', 'killed'].includes(task.status)) {
        await loadDigestDetail(date);
      } else {
        scheduleDigestAutoRefresh(date, taskId);
      }
    } catch {
      scheduleDigestAutoRefresh(date, taskId);
    }
  }, 4000);
}

function clearDigestAutoRefresh() {
  if (digestAutoRefreshTimer) {
    clearTimeout(digestAutoRefreshTimer);
    digestAutoRefreshTimer = null;
  }
}

// ── 工具函数 ──────────────────────────────────────────────

function fmtDigestTokens(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000)    return (n / 1000).toFixed(1) + 'k';
  return String(n || 0);
}
