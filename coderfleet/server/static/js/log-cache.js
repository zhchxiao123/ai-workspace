// ── 已完成任务日志的跨刷新持久缓存（IndexedDB）── issue #43
//
// done/failed/killed 任务的日志内容确定不再变化，但 _finishedLogCache（state.js）只是
// 内存里的 Map，页面一刷新就清空——刷新一次就要把这些任务的日志重新整份下载解析一遍。
// 这里加一层 IndexedDB 持久层：_fetchTaskLogCached() 命中内存缓存前先查 IndexedDB，
// 拉取到网络内容后既写内存缓存也落一份到这里，刷新页面后仍然命中。

const LOG_CACHE_DB_NAME    = 'coderfleet-log-cache';
const LOG_CACHE_STORE      = 'logs';
const LOG_CACHE_MAX_ENTRIES = 300; // 超过这个条数，按写入时间淘汰最老的，避免无限占用本地存储

let _logCacheDbPromise = null;

function _reqToPromise(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function _openLogCacheDb() {
  if (_logCacheDbPromise) return _logCacheDbPromise;
  _logCacheDbPromise = new Promise(resolve => {
    if (!window.indexedDB) { resolve(null); return; }
    let req;
    try {
      req = indexedDB.open(LOG_CACHE_DB_NAME, 1);
    } catch (e) { resolve(null); return; }
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(LOG_CACHE_STORE)) {
        const store = db.createObjectStore(LOG_CACHE_STORE, { keyPath: 'taskId' });
        store.createIndex('savedAt', 'savedAt');
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => resolve(null); // 拿不到 IndexedDB（隐私模式/被禁用等）就静默降级为无持久缓存
  });
  return _logCacheDbPromise;
}

// 读取某个任务缓存的日志正文；没有持久缓存环境或未命中都返回 null
async function logCacheGet(taskId) {
  const db = await _openLogCacheDb();
  if (!db) return null;
  try {
    const tx = db.transaction(LOG_CACHE_STORE, 'readonly');
    const record = await _reqToPromise(tx.objectStore(LOG_CACHE_STORE).get(taskId));
    return record ? record.text : null;
  } catch (e) {
    return null;
  }
}

// 清掉某个任务的持久缓存条目——发现缓存里是一份没收尾的半成品（工具调用状态卡死）
// 时用来自愈，逼下一次 _fetchTaskLogCached 老老实实重新拉一遍。
async function logCacheDelete(taskId) {
  const db = await _openLogCacheDb();
  if (!db) return;
  try {
    const tx = db.transaction(LOG_CACHE_STORE, 'readwrite');
    tx.objectStore(LOG_CACHE_STORE).delete(taskId);
  } catch (e) {
    return;
  }
}

// 写入某个任务的日志正文；失败（配额满/被禁用）静默忽略，不影响页面功能
async function logCacheSet(taskId, text) {
  const db = await _openLogCacheDb();
  if (!db) return;
  try {
    const tx = db.transaction(LOG_CACHE_STORE, 'readwrite');
    tx.objectStore(LOG_CACHE_STORE).put({ taskId, text, savedAt: Date.now() });
  } catch (e) {
    return;
  }
  _evictOldLogCacheEntries(db).catch(() => {});
}

async function _evictOldLogCacheEntries(db) {
  const tx = db.transaction(LOG_CACHE_STORE, 'readonly');
  const count = await _reqToPromise(tx.objectStore(LOG_CACHE_STORE).count());
  if (count <= LOG_CACHE_MAX_ENTRIES) return;

  const toEvict = count - LOG_CACHE_MAX_ENTRIES;
  const delTx = db.transaction(LOG_CACHE_STORE, 'readwrite');
  const index = delTx.objectStore(LOG_CACHE_STORE).index('savedAt');
  let deleted = 0;
  await new Promise(resolve => {
    const cursorReq = index.openCursor(); // savedAt 索引默认升序，最早写入的排最前
    cursorReq.onsuccess = () => {
      const cursor = cursorReq.result;
      if (!cursor || deleted >= toEvict) { resolve(); return; }
      cursor.delete();
      deleted++;
      cursor.continue();
    };
    cursorReq.onerror = () => resolve();
  });
}
