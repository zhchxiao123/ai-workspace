from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "coderfleet" / "server" / "static"
INDEX_HTML = STATIC_DIR / "index.html"
MOBILE_HTML = STATIC_DIR / "mobile.html"
PROJECTS_JS = STATIC_DIR / "js" / "projects.js"


def read_index() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def read_mobile() -> str:
    return MOBILE_HTML.read_text(encoding="utf-8")


def read_projects_js() -> str:
    return PROJECTS_JS.read_text(encoding="utf-8")


def read_ui_source() -> str:
    parts = [INDEX_HTML.read_text(encoding="utf-8")]
    for path in sorted((STATIC_DIR / "js").glob("*.js")):
        parts.append(path.read_text(encoding="utf-8"))
    parts.append((STATIC_DIR / "css" / "main.css").read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_dashboard_exposes_operational_summary() -> None:
    html = read_index()

    for element_id in [
        "metric-running",
        "metric-done",
        "metric-failed",
        "metric-accounts",
    ]:
        assert f'id="{element_id}"' in html


def test_task_page_embeds_submit_panel() -> None:
    html = read_index()
    source = read_ui_source()

    assert 'id="task-submit-panel"' in html
    assert 'id="task-submit-slot"' in html
    assert 'id="submit-modal"' in html
    assert 'id="submit-modal-slot"' in html
    assert "function openTaskSubmitPanel" in source
    assert "function openProjectSubmitModal" in source
    assert "function moveSubmitPanel" in source
    assert "function closeTaskSubmitPanel" in source
    assert "function closeSubmitModal" in source
    assert "submitContext" in source
    assert "submit-panel-close-btn" in html
    assert "populateConversations(convs, tasks, submitContext.projectName)" in source
    assert "populateProjects(projects, submitContext.projectName)" in source
    assert 'data-page="submit"' not in html
    assert 'id="page-submit"' not in html


def test_task_queue_uses_time_sorted_pagination_without_global_chain_board() -> None:
    source = read_ui_source()

    assert 'id="conversation-grid"' not in source
    assert "function renderConversationBoard" not in source
    assert 'id="task-pagination"' in source
    assert "TASK_PAGE_SIZE" in source
    assert "function setTaskPage" in source
    assert "new Date(b.created || 0) - new Date(a.created || 0)" in source
    assert 'class="task-desc-cell"' in source
    assert 'class="task-desc-wrap"' in source
    assert ".chain-badge" in source
    assert "text-overflow: ellipsis" in source
    assert "任务链视图" not in source


def test_dashboard_exposes_project_workspace() -> None:
    html = read_index()
    source = read_ui_source()

    assert 'data-page="projects"' in html
    assert 'id="project-grid"' in html
    assert 'id="project-detail-view"' in html
    assert 'data-project-filter="all"' not in source
    assert 'value="new-chain"' in html
    assert 'value="one-off"' in html
    assert 'id="project-status-filter"' not in source
    assert "t.status === 'running'" in source
    assert "t.status === 'done'" in source
    assert "t.status === 'failed'" in source
    assert 'id="project-list-view"' in html
    assert "project-detail-summary" in source
    assert "project-stats" in source
    assert "submitForCurrentProject" in source
    assert "function loadProjectsDashboard" in source
    assert "function renderProjectDetail" in source
    assert "function backToProjects" in source
    assert "function submitForCurrentProject" in source
    assert "function terminalWsUrl" in source


def test_legacy_project_records_use_single_canonical_path_owner() -> None:
    js = read_projects_js()

    assert "function canonicalProjectForLegacyRecord" in js
    assert "if (task.project_name) return task.project_name === project.name;" in js
    assert "if (conversation.project_name) return conversation.project_name === project.name;" in js
    assert "if (canonical) return canonical.name === project.name;" in js


def test_project_workspace_exposes_embedded_terminal() -> None:
    html = read_index()
    source = read_ui_source()

    assert '/static/vendor/xterm/xterm.css' in html
    assert '/static/vendor/xterm/xterm.js' in html
    assert '/static/vendor/xterm/addon-fit.js' in html
    assert 'id="project-detail-summary"' in html
    assert 'class="terminal-card"' in html
    assert 'class="terminal-toolbar"' in html
    assert 'id="project-terminal-status"' in html
    assert 'id="project-terminal"' in html
    assert 'id="project-terminal-reconnect"' in html
    assert "terminalContext" in source
    assert "function openProjectTerminal" in source
    assert "function connectProjectTerminal" in source
    assert "function disconnectProjectTerminal" in source
    assert "function resizeProjectTerminal" in source
    assert "window.addEventListener('beforeunload', disconnectProjectTerminal)" in source
    assert "disconnectProjectTerminal();" in source


def test_sidebar_can_collapse_with_persisted_state() -> None:
    html = read_index()
    source = read_ui_source()

    assert 'id="sidebar-toggle"' in html
    assert 'aria-label="折叠侧边栏"' in html
    assert 'aria-expanded="true"' in html
    assert "function toggleSidebar" in source
    assert "function applySidebarCollapsed" in source
    assert "localStorage.getItem('coderfleet.sidebarCollapsed')" in source
    assert "localStorage.setItem('coderfleet.sidebarCollapsed'" in source
    assert "sidebar-collapsed" in source
    assert "sidebar-label" in html
    assert "brand-text" in html
    assert "sidebar-toggle-icon" in html
    assert "/static/icons/logo-mark.png" in html
    assert html.index('class="brand-mark"') < html.index('class="brand-text"')
    assert 'width="30" height="30"' in html
    assert "main.css?v=20260525-auth" in html
    assert ".brand-mark img" in source
    assert ".layout.sidebar-collapsed .sidebar {\n      width: 56px;" in source
    assert ".layout.sidebar-collapsed .sidebar-toggle" in source


def test_account_cards_use_structured_resource_layout() -> None:
    source = read_ui_source()

    for class_name in [
        "account-card-head",
        "account-stats",
        "chip-list",
        "container-list",
    ]:
        assert class_name in source


def test_tool_log_icons_are_text_tokens_not_emoji() -> None:
    source = read_ui_source()
    match = re.search(r"const TOOL_ICONS = \{(?P<body>.*?)\};", source, re.S)

    assert match is not None
    assert not re.search(r"[\U0001F300-\U0001FAFF]", match.group("body"))


def test_icon_only_controls_have_accessible_names() -> None:
    html = read_index()

    assert 'id="follow-btn"' in html
    assert 'aria-label="切换日志跟踪"' in html
    assert 'aria-label="关闭日志"' in html


def test_log_modal_has_summary_and_timeline_shell() -> None:
    html = read_index()
    source = read_ui_source()

    for element_id in [
        "log-summary-status",
        "log-summary-account",
        "log-summary-project",
        "log-summary-created",
    ]:
        assert f'id="{element_id}"' in html

    assert 'class="log-shell"' in html
    assert 'class="log-summary"' in html
    assert "chat-log timeline" in source


def test_log_renderer_supports_opencode_events() -> None:
    source = read_ui_source()

    for token in [
        "case 'step_start'",
        "case 'tool_use'",
        "case 'text'",
        "case 'step_finish'",
        "_opencodeStepStart(d)",
        "_opencodeToolUse(d)",
        "_opencodeText(d)",
        "_opencodeStepFinish(d)",
        "_cleanOpenCodeResponse(text)",
        "_normalizeOpenCodeToolInput(name, input)",
        "oldString",
        "newString",
        "web_search",
        "THOUGHT:",
        "RESPONSE:",
    ]:
        assert token in source


def test_log_modal_uses_neutral_color_tokens() -> None:
    source = read_ui_source()

    for token in ["--log-bg", "--log-panel", "--log-card", "--log-card-soft"]:
        assert token in source


def test_chat_input_supports_pasted_image_uploads() -> None:
    source = read_ui_source()

    assert "textarea.addEventListener('paste', handleChatPaste)" in source
    assert "function handleChatPaste" in source
    assert "item.type.startsWith('image/')" in source
    assert "function normalizeImageFileForUpload" in source
    assert "new File([file]" in source
    assert 'id="chat-upload-status"' in source
    assert "图片还在上传中" in source
    assert "renderTaskImageAttachments(task)" in source


def test_chat_project_history_collapses_after_five_items() -> None:
    source = read_ui_source()

    assert "CHAT_PROJECT_VISIBLE_LIMIT = 5" in source
    assert "chatExpandedProjectNames" in source
    assert "items.slice(0, CHAT_PROJECT_VISIBLE_LIMIT)" in source
    assert "function toggleChatProjectItems" in source
    assert "chat-project-expand-btn" in source
    assert "展开显示" in source


def test_mobile_project_history_collapses_after_five_items_and_sorts_newest_first() -> None:
    source = read_mobile()

    assert "PROJECT_VISIBLE_LIMIT = 5" in source
    assert "expandedProjects" in source
    assert "collapsedProjects" in source
    assert "list.slice(0, PROJECT_VISIBLE_LIMIT)" in source
    assert ".sort((a, b) => convTimeValue(b) - convTimeValue(a))" in source
    assert "function toggleProjectGroup" in source
    assert "function toggleProjectItems" in source
    assert "展开显示" in source


def test_mobile_shell_uses_dynamic_viewport_height() -> None:
    source = read_mobile()

    assert "--app-height: 100dvh" in source
    assert "--keyboard-offset: 0px" in source
    assert "height: var(--app-height)" in source
    assert "transform: translateY(calc(-1 * var(--keyboard-offset)))" in source
    assert "function setViewportVars" in source
    assert "stableAppHeight" in source
    assert "layoutHeight - visibleHeight - viewportTop" in source
    assert "window.visualViewport.addEventListener('resize', setViewportVars)" in source


def test_mobile_inputs_avoid_focus_zoom_and_horizontal_drift() -> None:
    source = read_mobile()

    assert ".input-bar textarea" in source
    assert "font-size: 16px; line-height: 1.4" in source
    assert "overflow-y: auto; overflow-x: hidden" in source
    assert "max-width: 100vw" in source
    assert "overscroll-behavior: none" in source


def test_mobile_chat_supports_image_uploads_and_attachments() -> None:
    source = read_mobile()

    assert 'id="image-file"' in source
    assert 'accept="image/*"' in source
    assert "textarea.addEventListener('paste', handlePaste)" in source
    assert "function uploadImages" in source
    assert "/api/uploads?project_name=" in source
    assert "pendingImages.map(i => i.container_path)" in source
    assert "function renderImagePreviews" in source
    assert "function renderTaskImages" in source
    assert "user-msg-stack" in source
    assert "user-bubble-text" in source
    assert "user-image-attachments" in source
    assert "请查看附件图片。" in source


def test_site_uses_generated_logo_for_page_and_browser_icons() -> None:
    index = read_index()
    mobile = read_mobile()
    manifest = (STATIC_DIR / "manifest.json").read_text(encoding="utf-8")

    assert 'href="/static/icons/logo-mark.png"' in index
    assert 'src="/static/icons/logo-mark.png"' in index
    assert 'href="/static/icons/logo-mark.png"' in mobile
    assert '"/static/icons/logo-mark.png"' in manifest


def test_chat_project_groups_can_collapse() -> None:
    source = read_ui_source()

    assert "chatCollapsedProjectNames" in source
    assert "function toggleChatProjectGroup" in source
    assert "toggleChatProjectGroup('${encodedProjectName}')" in source
    assert "chat-project-group ${isCollapsed ? 'collapsed' : ''}" in source
    assert "proj-collapse-icon" in source
    assert "chat-project-group.collapsed .chat-project-items" in source


def test_chat_sidebar_can_collapse_independently() -> None:
    html = read_index()
    source = read_ui_source()

    assert 'id="chat-sidebar-toggle"' in html
    assert 'aria-label="隐藏对话侧边栏"' in html
    assert "function initChatSidebarState" in source
    assert "function toggleChatSidebar" in source
    assert "localStorage.getItem('coderfleet.chatSidebarCollapsed')" in source
    assert "localStorage.setItem('coderfleet.chatSidebarCollapsed'" in source
    assert "chat-sidebar-collapsed" in source


def test_workflow_templates_are_primary_and_runs_are_read_only() -> None:
    html = read_index()
    source = read_ui_source()

    assert html.index('id="wf-tab-templates"') < html.index('id="wf-tab-runs"')
    assert 'id="wf-panel-templates"' in html
    assert 'id="wf-panel-runs" style="display:none"' in html
    assert 'onclick="showNewPipelineModal()"' not in html
    assert 'onclick="showAddTaskModal(activePipelineId)"' not in html
    assert "dag-spawn-btn" not in source


def test_workflow_template_nodes_have_explicit_execution_targets() -> None:
    html = read_index()
    source = read_ui_source()

    assert "target_mode" in source
    assert "fixed_project" in source
    assert "runtime_role" in source
    assert "function _updateNodeTargetMode" in source
    assert "执行目标" in source
    assert 'id="run-tpl-default-project-group"' in html
    assert "data-role" in source


def test_workflow_template_connections_refresh_after_import() -> None:
    source = read_ui_source()

    assert "function _scheduleDrawflowConnectionRefresh" in source
    assert "requestAnimationFrame" in source
    assert "dfEditor.addConnection(from, to, 'output_1', 'input_1')" in source
    assert "suppressTemplateDirty" in source
    assert "drawflowImportVersion" in source
    assert "importVersion !== drawflowImportVersion" in source
    assert "updateConnectionNodes(`node-${id}`)" in source


def test_workflow_template_node_ids_are_deduplicated_before_save() -> None:
    source = read_ui_source()

    assert "function _nextTemplateNodeId" in source
    assert "function _dedupeTemplateNodeIds" in source
    assert "const nodeId = _nextTemplateNodeId()" in source
    assert "const data = _dedupeTemplateNodeIds(_getTemplateFromCanvas())" in source


def test_workflow_template_editor_is_not_polled_while_editing() -> None:
    source = read_ui_source()

    assert "currentPage === 'workflows' && wfActiveTab === 'runs'" in source
    interval = re.search(r"setInterval\(\(\) => \{(?P<body>.*?)\}, 5000\);", source, re.S)
    assert interval is not None
    assert "if (currentPage === 'workflows') loadWorkflows();" not in interval.group("body")
