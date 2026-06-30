'use strict';
// ── 工作区文件面板 ─────────────────────────────────────────

let _filePanelOpen = false;
let _filePanelProject = '';
let _filePanelPath = '';
let _filesResizeBound = false;

function toggleFilePanel() {
  if (_filePanelOpen) {
    closeFilePanel();
  } else {
    openFilePanel();
  }
}

function openFilePanel() {
  const panel = document.getElementById('chat-files-panel');
  const handle = document.getElementById('files-resize-handle');
  if (!panel) return;

  _filePanelOpen = true;
  _filePanelProject = currentChatProjectName || '';
  _filePanelPath = '';

  panel.style.display = 'flex';
  if (handle) handle.style.display = 'block';

  const btn = document.getElementById('files-panel-toggle-btn');
  if (btn) btn.classList.add('active');

  loadFilePanelDir('');
  _initFilesPanelResize();
}

function closeFilePanel() {
  const panel = document.getElementById('chat-files-panel');
  const handle = document.getElementById('files-resize-handle');
  if (!panel) return;

  _filePanelOpen = false;
  panel.style.display = 'none';
  if (handle) handle.style.display = 'none';

  const btn = document.getElementById('files-panel-toggle-btn');
  if (btn) btn.classList.remove('active');
}

// Called after workspace HTML is rebuilt to restore panel state
function restoreFilePanelIfOpen() {
  if (_filePanelOpen) {
    openFilePanel();
  }
}

async function loadFilePanelDir(relPath) {
  _filePanelPath = relPath;
  const tree = document.getElementById('files-panel-tree');
  const breadcrumb = document.getElementById('files-panel-breadcrumb');
  if (!tree) return;

  if (!_filePanelProject) {
    tree.innerHTML = '<div class="files-panel-empty">未选择项目</div>';
    return;
  }

  tree.innerHTML = '<div class="files-panel-empty">加载中...</div>';

  // Breadcrumb
  if (breadcrumb) {
    const parts = relPath ? relPath.split('/') : [];
    let html = `<span class="files-bc-item" onclick="loadFilePanelDir('')" title="根目录">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
    </span>`;
    let acc = '';
    parts.forEach((p, i) => {
      acc = acc ? acc + '/' + p : p;
      const captured = acc;
      html += `<span class="files-bc-sep">›</span>`;
      if (i === parts.length - 1) {
        html += `<span class="files-bc-item files-bc-current">${esc(p)}</span>`;
      } else {
        html += `<span class="files-bc-item" onclick="loadFilePanelDir('${esc(captured)}')">${esc(p)}</span>`;
      }
    });
    breadcrumb.innerHTML = html;
  }

  try {
    const qs = relPath ? `?path=${encodeURIComponent(relPath)}` : '';
    const resp = await fetch(`${API}/api/projects/${encodeURIComponent(_filePanelProject)}/files${qs}`);
    if (!resp.ok) throw new Error(await resp.text());
    const entries = await resp.json();
    _renderFilePanelTree(entries, relPath);
  } catch (e) {
    tree.innerHTML = `<div class="files-panel-empty" style="color:var(--danger)">加载失败: ${esc(String(e.message || e))}</div>`;
  }
}

function _renderFilePanelTree(entries, parentPath) {
  const tree = document.getElementById('files-panel-tree');
  if (!tree) return;

  let html = '';

  if (parentPath) {
    const up = parentPath.includes('/') ? parentPath.split('/').slice(0, -1).join('/') : '';
    html += `<div class="file-entry file-entry-back" onclick="loadFilePanelDir('${esc(up)}')">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5M12 5l-7 7 7 7"/></svg>
      <span>返回上级</span>
    </div>`;
  }

  if (!entries.length && !parentPath) {
    tree.innerHTML = '<div class="files-panel-empty">（空目录）</div>';
    return;
  }

  entries.forEach(e => {
    const icon = e.is_dir ? _folderIcon() : _fileIcon(e.name);
    if (e.is_dir) {
      html += `<div class="file-entry file-entry-dir" onclick="loadFilePanelDir('${esc(e.path)}')">
        ${icon}
        <span class="file-entry-name">${esc(e.name)}</span>
        <svg class="file-entry-chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>
      </div>`;
    } else {
      const size = e.size != null ? `<span class="file-entry-size">${_fmtSize(e.size)}</span>` : '';
      html += `<div class="file-entry file-entry-file" onclick="openFilePreview('${esc(e.path)}', '${esc(e.name)}')">
        ${icon}
        <span class="file-entry-name">${esc(e.name)}</span>
        ${size}
      </div>`;
    }
  });

  tree.innerHTML = html || '<div class="files-panel-empty">（空目录）</div>';
}

function _folderIcon() {
  return `<svg class="file-entry-icon file-entry-icon-dir" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 6.5A2.5 2.5 0 015.5 4H10l2 2h6.5A2.5 2.5 0 0121 8.5v8A2.5 2.5 0 0118.5 19h-13A2.5 2.5 0 013 16.5z"/></svg>`;
}

const _EXT_COLOR = {
  js:'#f0db4f', mjs:'#f0db4f', ts:'#3178c6', tsx:'#3178c6', jsx:'#61dafb',
  py:'#3572a5', css:'#563d7c', scss:'#cc6699', html:'#e34c26',
  json:'#cbcb41', md:'#4a90d9', txt:'#999', sh:'#89e051', bash:'#89e051',
  yaml:'#cb171e', yml:'#cb171e', toml:'#9c4221',
  png:'#9b59b6', jpg:'#9b59b6', jpeg:'#9b59b6', gif:'#9b59b6', svg:'#e67e22', webp:'#9b59b6',
  rs:'#ce422b', go:'#00acd7', java:'#b07219', c:'#555', cpp:'#f34b7d', h:'#555',
  rb:'#cc342d', php:'#4f5d95',
};

function _fileIcon(name) {
  const ext = name.split('.').pop().toLowerCase();
  const color = _EXT_COLOR[ext] || 'var(--text-3)';
  return `<svg class="file-entry-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.8"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
}

function _fmtSize(bytes) {
  if (bytes == null) return '';
  if (bytes < 1024) return bytes + 'B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'KB';
  return (bytes / 1024 / 1024).toFixed(1) + 'MB';
}

// ── 文件预览 ──────────────────────────────────────────────

async function openFilePreview(relPath, name) {
  const modal = document.getElementById('file-preview-modal');
  if (!modal || !_filePanelProject) return;

  const title = document.getElementById('file-preview-title');
  const body = document.getElementById('file-preview-body');
  const downloadBtn = document.getElementById('file-preview-download');

  if (title) title.textContent = name;
  if (body) body.innerHTML = '<div style="color:var(--text-3);padding:24px;font-size:13px;text-align:center">加载中...</div>';

  const dlUrl = authedUrl(`${API}/api/projects/${encodeURIComponent(_filePanelProject)}/download?path=${encodeURIComponent(relPath)}`);
  if (downloadBtn) {
    downloadBtn.href = dlUrl;
    downloadBtn.setAttribute('download', name);
  }

  modal.style.display = 'flex';

  const ext = name.split('.').pop().toLowerCase();
  const IMG = ['png','jpg','jpeg','gif','webp','svg','ico','bmp'];

  if (IMG.includes(ext)) {
    const imgUrl = authedUrl(`${API}/api/projects/${encodeURIComponent(_filePanelProject)}/preview?path=${encodeURIComponent(relPath)}`);
    if (body) body.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;padding:16px;background:var(--bg)"><img src="${imgUrl}" style="max-width:100%;max-height:100%;object-fit:contain;border-radius:4px" alt="${esc(name)}"></div>`;
    return;
  }

  try {
    const resp = await fetch(`${API}/api/projects/${encodeURIComponent(_filePanelProject)}/preview?path=${encodeURIComponent(relPath)}`);
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();

    let html = '';
    if (data.truncated) {
      html += `<div style="background:var(--surface-2);padding:5px 14px;font-size:11px;color:var(--text-3);border-bottom:1px solid var(--border)">⚠ 文件较大，仅显示前 512 KB</div>`;
    }

    const CODE_EXTS = new Set(['js','mjs','ts','tsx','jsx','py','css','scss','html','json','yaml','yml','toml','sh','bash','rs','go','java','c','cpp','h','rb','php','txt','ini','cfg','conf','env','lock','gitignore']);
    if (ext === 'md') {
      html += `<div class="file-preview-markdown">${marked.parse(data.content)}</div>`;
    } else if (CODE_EXTS.has(ext)) {
      html += `<pre class="file-preview-code">${esc(data.content)}</pre>`;
    } else {
      html += `<pre style="white-space:pre-wrap;word-break:break-all;font-size:12px;padding:16px;color:var(--text);margin:0">${esc(data.content)}</pre>`;
    }

    if (body) body.innerHTML = html;
  } catch (e) {
    if (body) body.innerHTML = `<div style="color:var(--text-3);padding:24px;font-size:13px;text-align:center">
      无法预览此文件（可能是二进制文件）<br>
      <a href="${dlUrl}" style="color:var(--accent);margin-top:8px;display:inline-block">↓ 直接下载</a>
    </div>`;
  }
}

function closeFilePreview() {
  const modal = document.getElementById('file-preview-modal');
  if (modal) modal.style.display = 'none';
}

// ── 拖拽调整面板宽度 ──────────────────────────────────────

function _initFilesPanelResize() {
  if (_filesResizeBound) return;
  _filesResizeBound = true;

  let dragging = false;
  let startX = 0;
  let startWidth = 0;

  document.addEventListener('pointerdown', e => {
    const handle = document.getElementById('files-resize-handle');
    if (!handle || e.target !== handle) return;
    dragging = true;
    startX = e.clientX;
    const panel = document.getElementById('chat-files-panel');
    startWidth = panel ? panel.getBoundingClientRect().width : 280;
    handle.setPointerCapture?.(e.pointerId);
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'ew-resize';
  });

  document.addEventListener('pointermove', e => {
    if (!dragging) return;
    const panel = document.getElementById('chat-files-panel');
    const row = document.querySelector('.chat-content-row');
    if (!panel || !row) return;
    const rowW = row.getBoundingClientRect().width;
    const delta = startX - e.clientX;
    const newW = Math.min(Math.max(200, startWidth + delta), rowW * 0.65);
    panel.style.width = newW + 'px';
    panel.style.flex = 'none';
  });

  document.addEventListener('pointerup', () => {
    if (!dragging) return;
    dragging = false;
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
  });
}
