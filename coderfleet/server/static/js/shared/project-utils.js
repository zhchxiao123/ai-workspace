// ══════════════════════════════════════════════════════════════
//  项目归属判断共享工具（desktop + mobile 共用）
//  消除 projects.js / chat.js / submit.js / terminal.js / mobile.html 中的重复实现
// ══════════════════════════════════════════════════════════════

/**
 * 规范化项目路径（去掉末尾斜杠）
 */
function normalizeProjectPath(path) {
  return String(path || '').replace(/\/+$/, '');
}

/**
 * 判断 recordPath 是否落在 projectPath 之下（支持子目录）
 */
function projectPathContains(projectPath, recordPath) {
  const base = normalizeProjectPath(projectPath);
  const target = normalizeProjectPath(recordPath);
  return !!base && (target === base || target.startsWith(base + '/'));
}

/**
 * 为遗留记录（无 project_name 的老数据）找到规范的项目对象
 * @param {object} record - 包含 project 字段的任务或会话
 * @param {Array|null} projects - 可选项目列表，默认回退到全局 projectsCache
 */
function canonicalProjectForLegacyRecord(record, projects = null) {
  const list = projects || (typeof projectsCache !== 'undefined' ? projectsCache : []);
  return (list || []).find(p => projectPathContains(p.path, record?.project)) || null;
}

/**
 * 遗留记录是否属于指定 project（处理老数据 project_name 缺失的情况）
 */
function legacyRecordBelongsToProject(record, project) {
  if (!projectPathContains(project?.path, record?.project)) return false;

  const canonical = canonicalProjectForLegacyRecord(record);
  if (canonical) return canonical.name === project.name;

  return true;
}

/**
 * 任务是否属于项目
 * 优先使用 project_name 精确匹配，回退到路径包含判断
 */
function taskBelongsToProject(task, project) {
  if (task.project_name) return task.project_name === project.name;
  return legacyRecordBelongsToProject(task, project);
}

/**
 * 会话是否属于项目
 * 优先使用 project_name 精确匹配，回退到路径包含判断
 */
function conversationBelongsToProject(conversation, project) {
  if (conversation.project_name) return conversation.project_name === project.name;
  return legacyRecordBelongsToProject(conversation, project);
}

// 暴露到全局（当前代码库使用经典 script 标签，无 ES Module）
if (typeof window !== 'undefined') {
  window.normalizeProjectPath = normalizeProjectPath;
  window.projectPathContains = projectPathContains;
  window.canonicalProjectForLegacyRecord = canonicalProjectForLegacyRecord;
  window.legacyRecordBelongsToProject = legacyRecordBelongsToProject;
  window.taskBelongsToProject = taskBelongsToProject;
  window.conversationBelongsToProject = conversationBelongsToProject;
}
