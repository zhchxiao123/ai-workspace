// ══════════════════════════════════════════════════════════════
//  文件上传共享逻辑（desktop chat + mobile 共用）
//  目标：网络上传循环 + 文件规范化 + 历史渲染 不再重复
// ══════════════════════════════════════════════════════════════

function _isImageMime(type) {
  return typeof type === 'string' && type.startsWith('image/');
}

function _isImagePath(path) {
  return /\.(png|jpe?g|gif|webp|bmp|svg|ico|tiff?|avif)$/i.test(path || '');
}

/**
 * 规范化无扩展名的文件（paste 场景常见）
 */
function normalizeFileForUpload(file, fromPaste = false) {
  const name = file.name || '';
  if (/\.[a-z0-9]+$/i.test(name)) return file;

  const extByType = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/gif': 'gif',
    'image/webp': 'webp',
    'image/bmp': 'bmp',
  };
  const ext = extByType[file.type] || (file.type ? file.type.split('/').pop() : 'bin');
  const prefix = fromPaste ? 'pasted-file' : 'upload-file';
  return new File([file], `${prefix}-${Date.now()}.${ext}`, { type: file.type || '' });
}

function normalizeImageFileForUpload(file, fromPaste = false) {
  return normalizeFileForUpload(file, fromPaste);
}

/**
 * 通用图片上传器（供桌面和移动各自的薄 wrapper 调用）
 *
 * @param {string} projectName
 * @param {File[]} files
 * @param {boolean} fromPaste
 * @param {object} callbacks
 *   - onFileUploaded(item)   // item = {container_path, preview_url, name}
 *   - onError(msg)
 *   - onProgressChange(uploadingCount)
 *   - getApiBase() => string   // 通常返回 API 常量
 */
async function uploadFilesGeneric(projectName, files, fromPaste, callbacks = {}) {
  const imageFiles = files.filter(f => f instanceof File || f instanceof Blob);
  if (!imageFiles.length || !projectName) return;

  const {
    onFileUploaded = () => {},
    onError = (m) => alert(m),
    onProgressChange = () => {},
    getApiBase = () => (typeof API !== 'undefined' ? API : '')
  } = callbacks;

  let uploading = imageFiles.length;
  onProgressChange(uploading);

  const API_BASE = getApiBase();

  for (const file of imageFiles) {
    try {
      const uploadFile = normalizeFileForUpload(file, fromPaste);
      const fd = new FormData();
      fd.append('file', uploadFile);

      const r = await fetch(
        `${API_BASE}/api/uploads?project_name=${encodeURIComponent(projectName)}`,
        { method: 'POST', body: fd }
      );
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || r.statusText);

      onFileUploaded({
        container_path: data.container_path,
        preview_url: data.preview_url,
        name: data.filename,
      });
    } catch (e) {
      onError(`上传文件失败: ${e.message || e}`);
    } finally {
      uploading -= 1;
      onProgressChange(uploading);
    }
  }
}

async function uploadImagesGeneric(projectName, files, fromPaste, callbacks = {}) {
  return uploadFilesGeneric(projectName, files, fromPaste, callbacks);
}

function _fileExtBadge(filename) {
  const ext = (filename.split('.').pop() || 'file').toUpperCase().slice(0, 6);
  const escFn = (s) => (typeof esc === 'function' ? esc(s) : String(s || ''));
  return `<span class="file-ext-badge">${escFn(ext)}</span>`;
}

/**
 * 渲染任务/消息中已上传文件的缩略图 HTML（桌面和移动历史区共用）
 */
function renderTaskFileAttachments(task, fallbackProjectName = '') {
  const images = task.images || [];
  if (!images.length) return '';

  const projectName = task.project_name || fallbackProjectName;
  if (!projectName) return '';

  const thumbs = images.map(path => {
    const filename = path.split('/').pop();
    const src = (typeof sseUrl === 'function')
      ? sseUrl(`${(typeof API !== 'undefined' ? API : '')}/api/uploads/${encodeURIComponent(projectName)}/${encodeURIComponent(filename)}`)
      : `/api/uploads/${encodeURIComponent(projectName)}/${encodeURIComponent(filename)}`;
    const safeName = typeof esc === 'function' ? esc(filename) : filename;
    if (_isImagePath(filename)) {
      return `<a class="user-image-attachment" href="${src}" target="_blank" rel="noopener" title="${safeName}"><img src="${src}" alt="${safeName}"></a>`;
    } else {
      return `<a class="user-file-attachment" href="${src}" target="_blank" rel="noopener" title="${safeName}">${_fileExtBadge(filename)}<span class="file-attach-name">${safeName}</span></a>`;
    }
  }).join('');

  return `<div class="user-image-attachments">${thumbs}</div>`;
}

function renderTaskImageAttachments(task, fallbackProjectName = '') {
  return renderTaskFileAttachments(task, fallbackProjectName);
}

// ══════════════════════════════════════════════════════════════
//  Phase 2 去重：pending 图片预览 + compose 上传流程共享
//  消除 chat.js 与 mobile.html 中的重复「上传 wrapper + 预览渲染」
// ══════════════════════════════════════════════════════════════

/**
 * 渲染 pendingImages 预览缩略图（参数化，支持 desktop/mobile 不同 DOM/CSS）
 */
function renderPendingPreviews(config = {}) {
  const {
    containerId = 'chat-image-previews',
    getImages = () => (Array.isArray(window.pendingImages) ? window.pendingImages : []),
    removeImage = (index) => {
      if (Array.isArray(window.pendingImages)) window.pendingImages.splice(index, 1);
    },
    clearImages = () => {
      if (Array.isArray(window.pendingImages)) window.pendingImages.length = 0;
    },
    afterRender = () => {},
    getSseUrl = (u) => (typeof sseUrl === 'function' ? sseUrl(u) : u),
    getApi = () => (typeof API !== 'undefined' ? API : ''),
    escFn = (s) => (typeof esc === 'function' ? esc(s) : String(s || '')),
  } = config;

  window.__pendingImageRemove = removeImage;
  window.__pendingImageClear = clearImages;
  window.__pendingImageRender = () => renderPendingPreviews(config);

  const container = document.getElementById(containerId);
  if (!container) return;

  const imgs = getImages() || [];
  if (!imgs.length) {
    container.style.display = 'none';
    container.innerHTML = '';
    afterRender();
    return;
  }
  container.style.display = 'flex';
  container.innerHTML = imgs.map((img, i) => {
    const safeName = escFn(img.name);
    const src = getSseUrl(`${getApi()}${img.preview_url}`);
    const removeBtn = `<button class="chat-attach-chip-remove" type="button" onclick="removePendingImage(${i})" title="移除">×</button>`;
    if (_isImagePath(img.name)) {
      return `<div class="chat-attach-chip" title="${safeName}"><img class="chat-attach-chip-thumb" src="${src}" alt="${safeName}"><span class="chat-attach-chip-name">${safeName}</span>${removeBtn}</div>`;
    } else {
      const ext = escFn((img.name.split('.').pop() || 'file').toUpperCase().slice(0, 6));
      return `<div class="chat-attach-chip" title="${safeName}"><span class="file-ext-badge">${ext}</span><span class="chat-attach-chip-name">${safeName}</span>${removeBtn}</div>`;
    }
  }).join('');
  afterRender();
}

/**
 * 移除指定 pending 图片（会调用当前环境的 renderPendingPreviews 或回退到全局 renderImagePreviews）
 */
function removePendingImage(index) {
  if (typeof window.__pendingImageRemove === 'function') {
    window.__pendingImageRemove(index);
  } else if (Array.isArray(window.pendingImages)) {
    window.pendingImages.splice(index, 1);
  }
  if (typeof window.__pendingImageRender === 'function') {
    window.__pendingImageRender();
  } else if (typeof window.renderImagePreviews === 'function') {
    window.renderImagePreviews();
  }
}

/**
 * 清空 pending 图片
 */
function clearPendingImages() {
  if (typeof window.__pendingImageClear === 'function') {
    window.__pendingImageClear();
  } else if (Array.isArray(window.pendingImages)) {
    window.pendingImages.length = 0;
  }
  if (typeof window.__pendingImageRender === 'function') {
    window.__pendingImageRender();
  } else if (typeof window.renderImagePreviews === 'function') {
    window.renderImagePreviews();
  }
}

/**
 * 通用「为当前对话/任务 compose 区上传图片」的高层封装
 * 各端（desktop/mobile）只需传入少量适配器即可复用完整流程。
 */
async function uploadFilesForCompose(files, fromPaste = false, adapters = {}) {
  const {
    getProjectName = () => '',
    onItemUploaded = (item) => {
      if (!Array.isArray(window.pendingImages)) window.pendingImages = [];
      window.pendingImages.push(item);
    },
    onProgressChange = () => {},
    onError = (m) => { if (typeof alert === 'function') alert(m); },
    getApiBase = () => (typeof API !== 'undefined' ? API : ''),
  } = adapters;

  const projectName = getProjectName();
  if (!projectName) {
    onError('请先选择项目后再上传文件');
    return;
  }

  await uploadFilesGeneric(projectName, files, fromPaste, {
    onFileUploaded: onItemUploaded,
    onError,
    onProgressChange,
    getApiBase,
  });
}

async function uploadImagesForCompose(files, fromPaste = false, adapters = {}) {
  return uploadFilesForCompose(files, fromPaste, adapters);
}

// 全局暴露（含 Phase 2 新增）
if (typeof window !== 'undefined') {
  window.normalizeFileForUpload = normalizeFileForUpload;
  window.normalizeImageFileForUpload = normalizeImageFileForUpload;
  window.uploadFilesGeneric = uploadFilesGeneric;
  window.uploadImagesGeneric = uploadImagesGeneric;
  window.renderTaskFileAttachments = renderTaskFileAttachments;
  window.renderTaskImageAttachments = renderTaskImageAttachments;
  window.renderPendingPreviews = renderPendingPreviews;
  window.removePendingImage = removePendingImage;
  window.clearPendingImages = clearPendingImages;
  window.uploadFilesForCompose = uploadFilesForCompose;
  window.uploadImagesForCompose = uploadImagesForCompose;
}
