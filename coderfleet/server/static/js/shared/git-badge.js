// ══════════════════════════════════════════════════════════════
//  Task 级 git 分支徽标渲染（issue #88）
//  目前只有 desktop chat.js 接入；mobile.html 未加载此文件，不在本次范围内。
// ══════════════════════════════════════════════════════════════

/**
 * 渲染 Task 级 git 分支徽标。
 * gitBranch 为空时返回空字符串——不渲染占位符，调用方直接拼进模板即可。
 * worktree 场景追加 "(worktree)" 后缀，跟 CLI `task status` 的展示保持一致（issue #87）。
 * diffAdded/diffRemoved 非零时追加 +N/-M（`git diff --shortstat HEAD`，相对上一次提交的
 * 未提交改动，不是相对默认分支的累计改动——跟 CLI 的"变更："行是同一份数据）。
 */
function renderGitBranchBadge(gitBranch, gitWorktree, diffAdded, diffRemoved) {
  if (!gitBranch) return '';
  const label = gitWorktree ? `${gitBranch} (worktree)` : gitBranch;
  const added = diffAdded || 0;
  const removed = diffRemoved || 0;
  const diffHtml = (added || removed)
    ? ` <span class="git-diff-stat"><span style="color:var(--green)">+${added}</span> <span style="color:var(--red)">-${removed}</span></span>`
    : '';
  return `<span class="git-branch-badge" title="${esc(label)}">🌿 ${esc(label)}</span>${diffHtml}`;
}

// 暴露到全局（当前代码库使用经典 script 标签，无 ES Module）
if (typeof window !== 'undefined') {
  window.renderGitBranchBadge = renderGitBranchBadge;
}
