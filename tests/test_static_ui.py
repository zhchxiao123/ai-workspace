from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "coderfleet" / "server" / "static"
INDEX_HTML = STATIC_DIR / "index.html"
MOBILE_HTML = STATIC_DIR / "mobile.html"
PROJECTS_JS = STATIC_DIR / "js" / "projects.js"
SCHEDULES_JS = STATIC_DIR / "js" / "schedules.js"
MAIN_PY = ROOT / "coderfleet" / "server" / "main.py"
SEARCH_PY = ROOT / "coderfleet" / "server" / "search.py"
RENDERER_CSS = STATIC_DIR / "css" / "renderer.css"


def read_index() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def read_mobile() -> str:
    return MOBILE_HTML.read_text(encoding="utf-8")


def read_projects_js() -> str:
    return PROJECTS_JS.read_text(encoding="utf-8")


def read_schedules_js() -> str:
    return SCHEDULES_JS.read_text(encoding="utf-8")


def read_main_py() -> str:
    return MAIN_PY.read_text(encoding="utf-8")


def read_renderer_css() -> str:
    return RENDERER_CSS.read_text(encoding="utf-8")


def read_ui_source() -> str:
    parts = [INDEX_HTML.read_text(encoding="utf-8")]
    # 包含顶层 js/*.js
    for path in sorted((STATIC_DIR / "js").glob("*.js")):
        parts.append(path.read_text(encoding="utf-8"))
    # 包含共享模块（Phase 0 架构去重后新增）
    for path in sorted((STATIC_DIR / "js" / "shared").glob("*.js")):
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


def test_project_form_exposes_docker_socket_override() -> None:
    html = read_index()
    source = read_projects_js()

    assert 'id="project-form-docker-socket"' in html
    assert "project-form-docker-socket" in source
    assert "docker_socket: dockerSocket" in source


def test_project_form_exposes_secondary_accounts() -> None:
    html = read_index()
    source = read_projects_js()

    # 选择器 + 下方已选列表（而非原生多选框），且选择器不包含已选为主账号的那个
    assert 'id="project-form-secondary-account-picker"' in html
    assert 'id="project-form-secondary-accounts-rows"' in html
    assert "multiple" not in re.search(
        r'<select id="project-form-secondary-account-picker".*?>', html
    ).group(0)
    assert "function addProjectSecondaryAccount" in source
    assert "function removeProjectSecondaryAccountRow" in source
    assert "function renderSecondaryAccountPicker" in source
    # 候选列表按 TYPE 过滤：排除主账号类型，也排除已选从账号的类型（同一项目不能绑两个同类型账号）
    assert "a.type !== primaryType" in source
    assert "!selectedTypes.has(a.type)" in source
    assert "secondary_accounts: _selectedSecondaryAccounts" in source

    # 已选从账号渲染为独立的 chip 行（而非借用 env-row/env-key，避免固定宽度错位）
    ui_source = read_ui_source()
    assert ".secondary-account-row {" in ui_source
    assert "secondary-account-row" in source
    assert "secondary-account-name" in source
    assert "secondary-account-remove" in source
    assert "env-row" not in re.search(
        r"function renderProjectSecondaryAccountsRows\(\)\s*\{.*?\n\}\n", source, re.S
    ).group(0)


def test_apply_modal_exposes_full_rebuild_opt_in_with_confirmation() -> None:
    html = read_index()
    source = read_ui_source()

    assert 'id="apply-full-checkbox"' in html
    assert "全量重建所有容器" in html
    assert "function startApply" in source
    assert "confirm(" in source
    assert "api/system/apply?full=" in source


def test_project_card_exposes_container_status_and_start_stop_controls() -> None:
    source = read_projects_js()

    assert "container_running" in source
    assert "function startProjectContainer" in source
    assert "function stopProjectContainer" in source
    assert "/container/start" in source
    assert "/container/stop" in source
    assert "onclick=\"event.stopPropagation();startProjectContainer(" in source
    assert "onclick=\"event.stopPropagation();stopProjectContainer(" in source


def test_start_project_container_shows_in_flight_state_and_toast_feedback() -> None:
    """issue #57：点击"启动"按钮要立即进入禁用 + "启动中…" 的中间态，请求结束后
    用共享 toast（而非 alert()）给出成功/失败反馈，并在两种结局下都恢复按钮。"""
    source = read_projects_js()

    assert "onclick=\"event.stopPropagation();startProjectContainer('${esc(project.name)}', this)\"" in source

    match = re.search(
        r"async function startProjectContainer\(name, btn\) \{(?P<body>.*?)\n\}\n", source, re.S,
    )
    assert match, "startProjectContainer(name, btn) not found"
    body = match.group("body")

    assert "btn.disabled = true" in body
    assert "启动中…" in body
    assert "showToast(" in body
    assert "alert(" not in body
    assert "btn.disabled = false" in body


def test_stop_project_container_shows_in_flight_state_and_toast_feedback() -> None:
    """issue #58：点击"停止"按钮要立即进入禁用 + "停止中…" 的中间态，请求结束后
    用共享 toast（而非 alert()）给出成功/失败反馈，并在两种结局下都恢复按钮。"""
    source = read_projects_js()

    assert "onclick=\"event.stopPropagation();stopProjectContainer('${esc(project.name)}', this)\"" in source

    match = re.search(
        r"async function stopProjectContainer\(name, btn\) \{(?P<body>.*?)\n\}\n", source, re.S,
    )
    assert match, "stopProjectContainer(name, btn) not found"
    body = match.group("body")

    assert "btn.disabled = true" in body
    assert "停止中…" in body
    assert "showToast(" in body
    assert "alert(" not in body
    assert "btn.disabled = false" in body


def test_saving_project_form_does_not_auto_start_container() -> None:
    source = read_projects_js()

    match = re.search(r"async function saveProjectForm\(\)\s*\{.*?\n\}\n", source, re.S)
    assert match, "saveProjectForm() not found"
    save_fn_body = match.group(0)
    assert "/container/start" not in save_fn_body
    assert "startProjectContainer" not in save_fn_body


def test_project_image_build_modal_exposes_manual_stop() -> None:
    html = read_index()
    source = read_projects_js()

    assert 'id="image-build-stop-btn"' in html
    assert "function stopProjectImageBuild" in source
    assert "AbortController" in source
    assert "/image/build/${encodeURIComponent(activeImageBuildId)}" in source


def test_project_build_history_panel_is_wired_up() -> None:
    html = read_index()
    source = read_projects_js()

    assert 'id="project-build-history-list"' in html
    assert "function loadProjectBuildHistory" in source
    assert "function viewBuildHistoryEntry" in source
    assert "/api/builds?project=" in source
    assert "/logs/stream?skip_bytes=" in source


def test_build_history_resume_uses_byte_length_not_char_length() -> None:
    """skip_bytes 是服务端日志文件的字节偏移量；构建日志里有中文，JS 字符串的
    .length（UTF-16 code unit 数）和 UTF-8 字节数对不上，必须用 TextEncoder 编码
    后的字节数，否则续传时会重复或漏掉内容。"""
    source = read_projects_js()

    assert "new TextEncoder().encode(output.textContent).length" in source
    assert "skip_bytes=${output.textContent.length}" not in source


def test_shared_image_build_trigger_is_wired_up() -> None:
    html = read_index()
    source = read_projects_js()

    assert 'onclick="buildSharedImage()"' in html
    assert 'id="shared-image-build-history-list"' in html
    assert "function buildSharedImage" in source
    assert "/api/image/build" in source
    assert "/api/builds?kind=shared" in source


def test_development_board_page_is_exposed() -> None:
    html = read_index()
    source = read_ui_source()

    assert 'data-page="boards"' in html
    assert 'id="page-boards"' in html
    assert 'id="board-shell"' in html
    assert 'id="board-modal"' in html
    assert 'id="board-name"' in html
    assert "deleteActiveBoard()" in html
    assert "/static/js/boards.js" in html
    assert "function loadBoards" in source
    assert "function openBoardModal" in source
    assert "function submitBoard" in source
    assert "function deleteActiveBoard" in source
    assert "function renderBoard" in source
    assert "function tasksForBoardCard" in source
    assert "task.board_card_id === card.id" in source
    assert "function submitForBoardCard" in source
    assert "function archiveBoardCard" in source
    assert "board_card_id" in source
    assert "editingBoardCardId" in source
    assert ".kanban-board" in source


def test_topbar_task_tab_scroll_buttons_hide_outside_chat() -> None:
    source = read_ui_source()

    assert "document.querySelectorAll('.topbar .tab-scroll-btn')" in source
    assert "btn.style.display = name === 'chat' ? '' : 'none'" in source


def test_legacy_project_records_use_single_canonical_path_owner() -> None:
    # Phase 0 后逻辑已迁移至共享模块，仍应在完整 UI source 中可找到
    source = read_ui_source()

    assert "canonicalProjectForLegacyRecord" in source
    assert "projectPathContains" in source
    assert "taskBelongsToProject" in source
    assert "conversationBelongsToProject" in source
    # 共享实现中的关键判断逻辑
    assert "if (task.project_name) return task.project_name === project.name" in source
    assert "if (conversation.project_name) return conversation.project_name === project.name" in source


def test_project_workspace_config_layout() -> None:
    """项目详情页改为配置管理布局：移除内嵌终端和 IDE iframe，保留 xterm 供底部终端使用。"""
    html = read_index()
    source = read_ui_source()

    # xterm 仍然加载（供底部终端/对话终端使用）
    assert '/static/vendor/xterm/xterm.css' in html
    assert '/static/vendor/xterm/xterm.js' in html
    assert '/static/vendor/xterm/addon-fit.js' in html

    # 项目配置卡片存在
    assert 'id="project-detail-summary"' in html
    assert 'id="project-env-card"' in html
    assert 'id="project-image-card"' in html
    assert 'project-config-grid' in html

    # 底部终端 JS 函数仍然存在（供对话终端/全局终端使用）
    assert "function disconnectProjectTerminal" in source or "disconnectProjectTerminal" in source


def test_bottom_terminal_tabs_keep_independent_sessions() -> None:
    source = read_ui_source()

    assert "function createBottomTerminalContext" in source
    assert "context: createBottomTerminalContext(id, projectName)" in source
    assert "function activateBottomTerminalTab" in source
    assert "bottomTerminalContext = tab.context" in source
    assert "className = 'bottom-terminal-pane'" in source
    assert "tab.context.socket || tab.context.terminal" in source
    assert "disconnectAllBottomTerminals" in source
    assert "window.addEventListener('beforeunload', disconnectAllBottomTerminals)" in source


def test_tmux_conversation_terminal_uses_local_scroll_and_selection_hold() -> None:
    source = read_ui_source()

    assert "function createEnhancedTerminal(mountEl, options = {})" in source
    assert "const interceptScroll = options.interceptScroll !== false" in source
    assert "function writeConversationTerminal(ctx, data)" in source
    assert "ctx.outputBacklog += text" in source
    assert "function flushConversationTerminalBacklog(ctx)" in source
    assert "terminal.onSelectionChange(() => {" in source
    assert "writeConversationTerminal(ctx, msg.data)" in source
    assert "ctx.socket.send(JSON.stringify({ type: 'scroll', lines }))" not in source
    assert "createEnhancedTerminal(mount)" in source
    assert "createEnhancedTerminal(pane)" in source


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
    assert "main.css?v=" in html
    assert ".brand-mark img" in source
    assert ".layout.sidebar-collapsed .sidebar {\n      width: 56px;" in source
    assert ".layout.sidebar-collapsed .sidebar-toggle" in source


def test_sidebar_system_controls_are_reorganized() -> None:
    html = read_index()
    source = read_ui_source()

    assert "brand-health" in html
    assert html.index("brand-health") < html.index("<nav>")
    assert html.index('id="health-dot"') < html.index("<nav>")

    footer_start = html.index('<div class="sidebar-footer">')
    footer_end = html.index("</aside>", footer_start)
    footer = html[footer_start:footer_end]

    assert 'data-page="settings"' in footer
    assert 'id="sidebar-toggle"' in footer
    assert 'id="health-dot"' not in footer
    assert 'id="pwa-notif-btn"' not in footer
    assert 'id="theme-toggle-btn"' not in footer

    settings_start = html.index('<div class="page" id="page-settings">')
    settings_end = html.index("</div>\n\n      </div>", settings_start)
    settings_page = html[settings_start:settings_end]

    assert 'id="settings-system-preferences"' in settings_page
    assert 'id="pwa-notif-btn"' in settings_page
    assert 'id="theme-toggle-btn"' in settings_page
    assert ".brand-health" in source
    assert ".sidebar-footer-actions" in source


def test_settings_page_uses_three_tab_sections() -> None:
    html = read_index()
    source = read_ui_source()

    settings_start = html.index('<div class="page" id="page-settings">')
    settings_end = html.index("</div>\n\n      </div>", settings_start)
    settings_page = html[settings_start:settings_end]

    assert 'class="settings-tabs"' in settings_page
    for tab in ["preferences", "runtime", "account"]:
        assert f'data-settings-tab="{tab}"' in settings_page
        assert f'id="settings-panel-{tab}"' in settings_page
        assert f'aria-controls="settings-panel-{tab}"' in settings_page

    assert 'id="settings-runtime-wrap"' in settings_page
    assert 'id="settings-account-wrap"' in settings_page
    assert 'id="settings-system-preferences"' in settings_page

    assert "function switchSettingsTab" in source
    assert "function initSettingsPage" in source
    assert "function loadRuntimeSettings" in source
    assert "function refreshCurrentSettingsTab" in source
    assert "localStorage.getItem('coderfleet.settings.activeTab')" in source
    assert "localStorage.setItem('coderfleet.settings.activeTab'" in source
    assert "settings-tab.active" in source
    assert "settings-panel.active" in source


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


def test_log_renderer_supports_grok_text_data_events() -> None:
    source = read_ui_source()

    for token in [
        "case 'thought':",
        "this._grokToken('thought', d.data || '')",
        "case 'text':",
        "this._grokToken('text',    d.data || '')",
        "case 'end':",
        "d.sessionId || d.session_id || d.sessionID",
        "_grokFlushCurrent()",
    ]:
        assert token in source


def test_chat_optimistic_renderer_receives_account_type() -> None:
    source = read_ui_source()

    for token in [
        "function _appendOptimisticUserMessage(promptText, snapshotPaths, taskId, isPending = false, accountType = '')",
        "localRenderer.accountType = accountType || (tasksCache || []).find(t => t.id === taskId)?.type || ''",
        "_appendOptimisticUserMessage(promptText, snapshotPaths, taskData.id, willPend1, taskData.type)",
        "_appendOptimisticUserMessage(promptText, snapshotPaths, data.id, willPend, data.type)",
    ]:
        assert token in source


def test_log_renderer_reassembles_multiline_json_events() -> None:
    source = read_ui_source()

    for token in [
        "isFooterSeparator",
        "next.startsWith('finished:') || next.startsWith('usage status:')",
        "_renderBody(lines)",
        "_processJsonBodyText(text)",
        "let jsonStart = -1",
        "depth += 1",
        "this._event(JSON.parse(candidate))",
    ]:
        assert token in source


def test_log_modal_uses_neutral_color_tokens() -> None:
    source = read_ui_source()

    for token in ["--log-bg", "--log-panel", "--log-card", "--log-card-soft"]:
        assert token in source
    assert "--log-bg: #ffffff" in source
    assert "html[data-theme=\"dark\"]" in source
    assert "--log-bg: #111827" in source
    assert "color:var(--text-3);font-size:12px;padding:20px" in source


def test_chat_input_supports_pasted_image_uploads() -> None:
    source = read_ui_source()

    assert "textarea.addEventListener('paste', handleChatPaste)" in source
    assert "function handleChatPaste" in source
    assert "item.type.startsWith('image/')" in source
    # Phase 0 后 normalize 迁移至共享模块
    assert "normalizeImageFileForUpload" in source
    assert "uploadImagesGeneric" in source
    assert "new File([file]" in source
    assert 'id="chat-upload-status"' in source
    assert "图片还在上传中" in source
    assert "renderTaskImageAttachments(task" in source  # 由 shared/image-upload.js 提供，chat.js 直接调用
    assert "shared/image-upload.js" in source  # 脚本已引入


def test_chat_scheduled_send_requires_future_time_and_refreshes_on_start() -> None:
    source = read_ui_source()

    assert "function getChatScheduleExecuteAt" in source
    assert "throw new Error('请选择定时发送时间')" in source
    assert "throw new Error('定时发送时间必须晚于当前时间')" in source
    assert "function ensureChatScheduleDefault" in source
    assert "Date.now() + 5 * 60 * 1000" in source
    assert "shouldRefreshActiveChatForTaskStatusChange(tasks)" in source
    assert "selectConversation(activeConversationId)" in source


def test_chat_can_select_cli_model_from_header_pill() -> None:
    source = read_ui_source()

    assert 'id="chat-model-select"' in source
    assert "class=\"chat-model-pill\"" in source
    assert "function getChatAccountType" in source
    assert "function buildChatModelPillHtml" in source
    assert "function onChatModelChange" in source
    assert "function getChatModel" in source
    assert "const model = getChatModel()" in source
    assert "model," in source
    assert ".chat-model-pill" in source
    assert "chatModelByConversation" in source
    # 仅 Claude 类型账号支持 --model，其余账号类型不显示模型选择徽章
    assert "chatAccountType === 'claude'" in source


def test_chat_queue_marks_pending_and_scheduled_items() -> None:
    source = read_ui_source()

    assert "chat-queue-status" in source
    assert "statusLabel = isScheduled ? '定时' : '排队'" in source
    assert "等待上一条任务完成" in source
    assert "定时发送：" in source
    assert ".chat-queue-status.scheduled" in source
    assert ".chat-queue-status.pending" in source


def test_chat_project_history_collapses_after_five_items() -> None:
    source = read_ui_source()

    assert "CHAT_PROJECT_VISIBLE_LIMIT = 5" in source
    assert "chatExpandedProjectNames" in source
    assert "filteredItems.slice(0, CHAT_PROJECT_VISIBLE_LIMIT)" in source
    assert "function toggleChatProjectItems" in source
    assert "chat-project-expand-btn" in source
    assert "展开显示" in source


def test_chat_search_uses_global_search_results_not_tree_filter_only() -> None:
    html = read_index()
    source = read_ui_source()
    main_py = read_main_py()

    assert 'placeholder="搜索项目、对话、任务或内容..."' in html
    assert '@app.get("/api/search", response_model=SearchResponse)' in main_py
    # SearchResult 现由 search 深模块持有（逻辑已移出 HTTP 处理器）
    assert "class SearchResult" in SEARCH_PY.read_text(encoding="utf-8")
    assert "fetch(`${API}/api/search?${params.toString()}`)" in source
    assert "function renderChatSearchResults" in source
    assert "function openChatSearchResult" in source
    assert "runChatDeepSearch" in source
    assert "chatSearchResults" in source


def test_chat_composer_supports_at_mention_file_autocomplete() -> None:
    source = read_ui_source()
    main_py = read_main_py()
    search_py = SEARCH_PY.read_text(encoding="utf-8")

    assert 'id="chat-mention-dropdown"' in source
    assert "function _detectChatMentionToken" in source
    assert "_handleChatMentionInput(textarea)" in source
    assert "function _performChatMentionSearch" in source
    assert "function _renderChatMentionDropdown" in source
    assert "function _confirmChatMentionSelection" in source
    assert "function _closeChatMentionDropdown" in source
    assert "function _validateChatMentionCursor" in source
    assert "/files/search?${params.toString()}" in source
    assert "chatMentionOpen" in source

    assert '@app.get("/api/projects/{project_name}/files/search"' in main_py
    assert "def rank_paths" in search_py


def test_mobile_project_history_collapses_after_five_items_and_sorts_newest_first() -> None:
    source = read_mobile()

    assert "PROJECT_VISIBLE_LIMIT = 5" in source
    assert "expandedProjects" in source
    assert "collapsedProjects" in source
    assert "list.slice(0, PROJECT_VISIBLE_LIMIT)" in source
    assert "function projectDevItems" in source
    assert "return [...convItems, ...oneOffItems].sort((a, b) => b.time - a.time)" in source
    assert "function toggleProjectGroup" in source
    assert "function toggleProjectItems" in source
    assert "展开显示" in source


def test_mobile_exposes_global_chat_search() -> None:
    source = read_mobile()

    assert 'id="mobile-search-input"' in source
    assert "function performMobileSearch" in source
    assert "function renderMobileSearchResults" in source
    assert "function openMobileSearchResult" in source
    assert "api(`/api/search?${params.toString()}`)" in source
    assert "runMobileDeepSearch" in source


def test_mobile_development_controls_cover_task_lifecycle() -> None:
    source = read_mobile()

    assert 'id="auto-mode"' in source
    assert 'id="schedule-mode"' in source
    assert 'id="schedule-time"' in source
    assert "function currentScheduleTime" in source
    assert "execute_at: executeAt" in source
    assert "function taskActionsHtml" in source
    assert "function killMobileTask" in source
    assert "function retryMobileTask" in source
    assert "取消排队" in source
    assert "取消定时" in source
    assert "重试" in source


def test_mobile_shows_one_off_tasks_and_can_upgrade_to_conversation() -> None:
    source = read_mobile()

    assert "type: 'one-off'" in source
    assert "function oneOffRow" in source
    assert "function openOneOff" in source
    assert "isOneOff: true" in source
    assert "function upgradeOneOffToConversation" in source
    assert "task_id: taskId" in source
    assert "一次性任务" in source


def test_mobile_conversation_actions_and_terminal_entry_exist() -> None:
    source = read_mobile()

    assert 'id="action-sheet"' in source
    assert "function showItemActions" in source
    assert "function archiveActionTarget" in source
    assert "function deleteActionTarget" in source
    assert "归档" in source
    assert 'id="v-terminal"' in source
    assert 'id="mobile-terminal"' in source
    assert "/static/vendor/xterm/xterm.js" in source
    assert "function openMobileTerminal" in source
    assert "function connectMobileTerminal" in source
    assert "function sendTerminalKey" in source
    assert "/api/projects/${encodeURIComponent(projectName)}/terminal" in source


def test_project_page_embeds_ide_iframe() -> None:
    html = read_index()
    source = read_projects_js()

    assert 'id="project-ide-frame"' in html
    assert "function renderProjectIde" in source
    assert "function openProjectIdeWindow" in source
    assert "ide_enabled" in source
    assert "ide_port" in source


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
    # Phase 0 后使用共享 uploadImagesGeneric
    ui_source = read_ui_source()
    assert ("uploadImagesGeneric" in ui_source or "/api/uploads?project_name=" in ui_source)
    assert "pendingImages.map(i => i.container_path)" in source
    assert "function renderImagePreviews" in source
    assert "function renderTaskImages" in source
    assert "user-msg-stack" in source
    assert "user-bubble-text" in source
    assert "user-image-attachments" in source
    assert "请查看附件图片。" in source
    assert "shared/image-upload.js" in source  # 移动端已引入共享


def test_mobile_renders_cf_send_download_cards() -> None:
    source = read_mobile()
    renderer_css = read_renderer_css()

    assert "new ChatLogRenderer(wrap, false, true, task.project_name || null, task.id)" in source
    assert "new ChatLogRenderer(wrap, true, true, task.project_name || null, task.id)" in source
    assert ".cf-download-card" in renderer_css
    assert ".cf-dl-btn" in renderer_css


def test_mobile_running_chat_hydrates_history_before_streaming() -> None:
    source = read_mobile()

    assert "existing.dataset.status !== (task.status || '')" in source
    assert "existing.replaceWith(replacement)" in source
    assert "const txt = await fetch(`/api/tasks/${id}/logs`).then(r => r.ok ? r.text() : '')" in source
    assert "skipBytes = new TextEncoder().encode(txt).byteLength" in source
    assert "skip_bytes=${skipBytes}" in source
    assert "logs/stream?${qs}" in source


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


def test_mobile_no_longer_duplicates_shared_helpers_after_phase1() -> None:
    """Phase 1 后，mobile.html 不再重复定义已迁移至 utils.js 的小工具。"""
    mobile = read_mobile()

    # 这些函数现在只应由 utils.js 提供，mobile 仅调用（不重新定义）
    assert "function x(s)" not in mobile
    assert "function stripAnsi(s)" not in mobile
    assert "function relTime(iso)" not in mobile

    # 但它们仍应在完整 UI 源中存在（通过 utils.js）
    ui_source = read_ui_source()
    assert "function x(s)" in ui_source
    assert "function stripAnsi(s)" in ui_source
    assert "function relTime(iso)" in ui_source


def test_mobile_no_longer_duplicates_pending_image_logic_after_phase2() -> None:
    """Phase 2 后，mobile.html 与 chat.js 不再重复 pendingImages 预览/上传 wrapper 逻辑。"""
    mobile = read_mobile()
    ui_source = read_ui_source()

    # 共享函数应存在
    assert "function uploadImagesForCompose" in ui_source or "uploadImagesForCompose =" in ui_source
    assert "function renderPendingPreviews" in ui_source or "renderPendingPreviews =" in ui_source
    assert "function removePendingImage" in ui_source or "removePendingImage =" in ui_source

    # mobile 不再自己实现完整的上传 wrapper（而是委托给共享）
    # （旧的 uploadImagesGeneric 直接调用块应被 uploadImagesForCompose 替换）
    assert "uploadImagesGeneric(\n    projectName," not in mobile
    assert "await uploadImagesGeneric(" not in mobile or "uploadImagesForCompose" in mobile

    # 预览渲染函数在 mobile 中应为薄 wrapper（不包含完整 map 模板字符串的旧实现）
    # 旧实现特征：大量内联的 image-thumb 模板
    assert 'class="image-thumb"' not in mobile or "renderPendingPreviews" in mobile


def test_shared_pending_image_preview_uses_surface_state_adapters() -> None:
    """共享预览不能直接依赖 window.pendingImages；桌面/移动的 let 状态不会挂到 window。"""
    mobile = read_mobile()
    ui_source = read_ui_source()

    assert "getImages = () =>" in ui_source
    assert "removeImage = (index)" in ui_source
    assert "window.__pendingImageRender" in ui_source
    assert "window.renderPendingPreviews();" not in ui_source
    assert "getImages: () => pendingImages" in ui_source
    assert "getImages: () => pendingImages" in mobile


def test_workflow_template_editor_is_not_polled_while_editing() -> None:
    source = read_ui_source()

    assert "currentPage === 'workflows' && wfActiveTab === 'runs'" in source
    interval = re.search(r"setInterval\(\(\) => \{(?P<body>.*?)\}, 5000\);", source, re.S)
    assert interval is not None
    assert "if (currentPage === 'workflows') loadWorkflows();" not in interval.group("body")


def test_select_conversation_fetches_scoped_tasks_instead_of_full_reload() -> None:
    """切换会话不该把全部会话+全部项目+最多1000条任务重新拉一遍；
    应该用已有缓存直接渲染，只按 conversation_id 拉这一个会话的任务。"""
    source = read_ui_source()

    match = re.search(
        r"async function selectConversation\(convId\) \{(?P<body>.*?)\n\}\n\n", source, re.S,
    )
    assert match is not None
    body = match.group("body")

    assert "api/tasks?conversation_id=" in body
    assert "renderChatWorkspace(conv)" in body


def test_chat_poll_uses_heartbeat_instead_of_full_reload() -> None:
    """5 秒轮询不该无脑重新拉全部会话+全部任务；应该先问一次轻量心跳，
    真的有状态变化才触发一次全量刷新。"""
    source = read_ui_source()

    interval = re.search(r"setInterval\(\(\) => \{(?P<body>.*?)\}, 5000\);", source, re.S)
    assert interval is not None
    body = interval.group("body")

    assert "if (currentPage === 'chat' && !chatFollowMode) loadConversations(false);" not in body
    assert "pollChatHeartbeat" in body
    assert "async function pollChatHeartbeat" in source
    assert "api/tasks/heartbeat" in source


def test_chat_reuses_cached_log_for_finished_tasks_instead_of_refetching() -> None:
    """done/failed/killed 任务的日志内容不会再变；会话因为其它任务状态变化整体
    重渲染时，不该把这些历史日志重新拉一遍（issue #42 里抽成了 _fetchTaskLogCached，
    eager 加载和折叠占位点开懒加载两条路径共用同一份缓存逻辑）。"""
    source = read_ui_source()

    assert "_finishedLogCache" in source
    assert "_finishedLogCache.has(task.id)" in source
    assert "_finishedLogCache.set(task.id, text)" in source
    assert "async function _fetchTaskLogCached(task)" in source


def test_render_chat_workspace_only_eager_fetches_recent_task_logs() -> None:
    """issue #42：打开/刷新一个会话不该把全部历史任务的日志一次性拉下来渲染——
    历史越长（比如 /loop 跑出来几十轮），日志累计能到几 MB，一次性全部请求+渲染
    会把首屏卡住。只应该 eager 拉取渲染最近 CHAT_EAGER_LOG_COUNT 条，更早的任务
    显示折叠占位，点开才按需拉取。"""
    source = read_ui_source()

    assert re.search(r"const\s+CHAT_EAGER_LOG_COUNT\s*=\s*\d+", source)

    match = re.search(
        r"async function renderChatWorkspace\(conv\) \{(?P<body>.*?)\n\}\n\n", source, re.S,
    )
    assert match is not None
    body = match.group("body")

    assert "collapsedTasks" in body
    assert "eagerTasks" in body
    assert "CHAT_EAGER_LOG_COUNT" in body
    assert "_renderCollapsedTaskPlaceholder(task)" in body
    assert "_fetchTaskLogCached(t)" in body
    # 折叠占位不该在打开会话时就去拉日志正文——只有 eagerTasks 才在 Promise.all 里请求
    assert "eagerTasks.map(t => _fetchTaskLogCached(t))" in body

    assert "function _renderCollapsedTaskPlaceholder(task)" in source
    # 点开折叠占位才按需拉取渲染，复用同一份 _fetchTaskLogCached 缓存逻辑
    collapsed_match = re.search(
        r"function _renderCollapsedTaskPlaceholder\(task\) \{(?P<body>.*?)\n\}\n\n", source, re.S,
    )
    assert collapsed_match is not None
    assert "_fetchTaskLogCached(task)" in collapsed_match.group("body")


def test_manually_expanded_historical_task_survives_workspace_rerender() -> None:
    """issue #42 code-review 补充：renderChatWorkspace 每次重渲染（比如心跳触发的整体
    重渲染）都会把 chatContent 从 collapsedTasks/eagerTasks 重新整个建一遍。如果不记录
    "用户手动点开过哪些折叠任务"，展开过的历史任务会在下一次重渲染时又变回折叠占位。"""
    source = read_ui_source()

    assert "chatExpandedTaskIds" in source
    assert re.search(r"const\s+chatExpandedTaskIds\s*=\s*new Set\(\)", source)

    # 点开折叠占位时要记进这个集合
    collapsed_match = re.search(
        r"function _renderCollapsedTaskPlaceholder\(task\) \{(?P<body>.*?)\n\}\n\n", source, re.S,
    )
    assert collapsed_match is not None
    assert "chatExpandedTaskIds.add(task.id)" in collapsed_match.group("body")

    # renderChatWorkspace 要读取这个集合，把命中的历史任务当作展开态渲染，而不是折叠占位
    match = re.search(
        r"async function renderChatWorkspace\(conv\) \{(?P<body>.*?)\n\}\n\n", source, re.S,
    )
    assert match is not None
    body = match.group("body")
    assert "manuallyExpanded" in body
    assert "chatExpandedTaskIds.has(t.id)" in body
    assert "expandedLogsById" in body


def test_finished_task_logs_persist_across_page_reloads_via_indexeddb() -> None:
    """issue #43：_finishedLogCache 只是内存 Map，刷新页面就清空，已完成任务的日志
    每次刷新都要重新整份下载。_fetchTaskLogCached 应该在内存缓存未命中时，
    先查 IndexedDB 持久层，命中就不必发网络请求；拉取到新内容后两层缓存都要写。"""
    source = read_ui_source()

    assert '<script src="/static/js/log-cache.js' in INDEX_HTML.read_text(encoding="utf-8")

    assert "async function logCacheGet(taskId)" in source
    assert "async function logCacheSet(taskId, text)" in source
    # 有淘汰策略，避免无限占用本地存储
    assert re.search(r"LOG_CACHE_MAX_ENTRIES\s*=\s*\d+", source)
    assert "_evictOldLogCacheEntries" in source

    match = re.search(
        r"async function _fetchTaskLogCached\(task\) \{(?P<body>.*?)\n\}\n\n", source, re.S,
    )
    assert match is not None
    body = match.group("body")

    assert "logCacheGet(task.id)" in body
    assert "logCacheSet(task.id, text)" in body
    # running 任务的日志还在变化，不该走持久缓存
    assert "task.status !== 'running'" in body


def test_open_pipeline_does_not_refetch_full_task_list() -> None:
    """loadWorkflows() 进入工作流 tab 时已经拉过 workflowTasksCache；点击某个工作流
    不该在 openPipeline() 里重新拉一次 /api/tasks?limit=300——详情所需的任务已经
    内嵌在 /api/workflow-runs/{id} 的响应里。"""
    source = read_ui_source()

    match = re.search(
        r"async function openPipeline\(id\) \{(?P<body>.*?)\n\}",
        source, re.S,
    )
    assert match is not None
    body = match.group("body")

    assert "api/tasks?limit=300" not in body
    assert "api/workflow-runs/${id}" in body
    assert "renderPipelineList(pipelinesCache, workflowTasksCache)" in body
    assert "_renderActivePipeline(pipeline, pipeline.tasks" in body


def test_shared_toast_helper_is_promoted_to_utils_and_reused_by_workflow_toasts() -> None:
    """issue #56：项目启动/停止按钮（#57/#58）要复用 workflow 场景已有的浮层提示，
    而不是各自重新实现一份。把 workflows.js 里的 _wfToast 提升为 utils.js 的通用
    showToast(message, type)，workflows.js 改为调用共享实现，不再本地定义。"""
    utils_source = (STATIC_DIR / "js" / "utils.js").read_text(encoding="utf-8")
    workflows_source = (STATIC_DIR / "js" / "workflows.js").read_text(encoding="utf-8")

    assert "function showToast(message, type" in utils_source
    assert "toast-host" in utils_source

    assert "function _wfToast" not in workflows_source
    assert "showToast(" in workflows_source

    ui_source = read_ui_source()
    assert "#toast-host" in ui_source
    assert ".toast-success" in ui_source
    assert ".toast-error" in ui_source
    assert ".wf-toast" not in ui_source


def test_open_template_renders_before_projects_and_accounts_resolve() -> None:
    """issue #40：打开模板只需要 /api/workflow-templates/{id} 就能渲染 DAG；
    projects/accounts 只在节点详情面板和"运行模板"弹窗里用得上，不该用 Promise.all
    卡住模板渲染，且 loadTemplates() 已经拉过的缓存不该重新请求。"""
    source = read_ui_source()

    match = re.search(
        r"async function openTemplate\(id\) \{(?P<body>.*?)\n\}",
        source, re.S,
    )
    assert match is not None
    body = match.group("body")

    assert "Promise.all" not in body
    assert "_renderTemplateEditor(tpl)" in body
    assert "if (!projectsCache.length)" in body
    assert "if (!workflowAccountsCache.length)" in body

    render_idx = body.index("_renderTemplateEditor(tpl)")
    projects_idx = body.index("if (!projectsCache.length)")
    assert render_idx < projects_idx, "模板渲染必须先于 projects 懒加载执行"


def test_schedule_card_flags_agent_created_schedules() -> None:
    """issue #82：Schedule 新增 created_by（human|agent）后，Web UI 列表必须显示出来——
    不能是个只写不读的字段（CLAUDE.md 的字段必须端到端接线铁律，历史上 Board.project_name
    等字段就因为漏了这一步被专门开票清理过）。"""
    source = read_schedules_js()

    match = re.search(r"function scheduleCard\(s\) \{(?P<body>.*?)\n\}", source, re.S)
    assert match is not None
    body = match.group("body")
    assert "s.created_by" in body
    assert "agent" in body


def test_git_branch_badge_helper_skips_empty_branch() -> None:
    """issue #88：徽标渲染是一个纯函数，分支为空时必须直接返回空字符串——
    不渲染任何占位符/错误提示（PRD 明确要求：没有 git 信息就不显示徽标）。"""
    source = read_ui_source()

    match = re.search(
        r"function renderGitBranchBadge\(gitBranch, gitWorktree\) \{(?P<body>.*?)\n\}",
        source, re.S,
    )
    assert match is not None
    body = match.group("body")
    assert "if (!gitBranch) return '';" in body
    assert "worktree" in body


def test_chat_sidebar_and_header_render_git_branch_badge() -> None:
    """issue #88：分支徽标只应该出现在两个既有展示点——侧边栏会话/任务行、
    以及会话工作区头部副标题——都复用同一个共享 renderGitBranchBadge helper，
    不各自重新拼 HTML（避免issue #87 Spec review 里点名的重复实现）。会话场景按
    conv.last_task_id 查 Task（而不是"最近创建的 Task"），跟 issue #88 验收标准
    明确要求的读取方式一致——issue #87 Spec review 曾指出实现偏离过这一点。"""
    source = read_ui_source()

    # 定义 1 处（shared/git-badge.js）+ 2 个调用点：侧边栏行、
    # _gitBranchBadgeForConversation（两个头部渲染函数共用它，不各自重复调用）
    assert source.count("renderGitBranchBadge(") >= 3
    assert "function _gitBranchBadgeForConversation(conv)" in source
    assert "conv?.last_task_id ? (tasksCache || []).find(t => t.id === conv.last_task_id)" in source
    assert "gitBranch: lastTask?.git_branch || ''" in source
    assert "gitBranch: t.git_branch || ''" in source
    # 徽标紧跟在"项目: xxx"后面（issue #88 原文："在已有'项目: xxx'信息旁追加"），
    # 不是随手拼在副标题末尾——两个头部渲染函数都要满足。
    assert source.count("</strong>${headerBranchBadge} ·") == 2


def test_recent_section_and_quick_jump_omit_git_branch_badge() -> None:
    """issue #88 明确划定范围：'最近访问' 区块和 Ctrl+K 快速跳转保持现有信息密度，
    不追加分支徽标——只有侧边栏项目分组内的行和会话头部两处展示。"""
    source = read_ui_source()

    recent_match = re.search(
        r"function renderRecentSection\(convs, projects, tasks\) \{(?P<body>.*?)\n\}",
        source, re.S,
    )
    assert recent_match is not None
    assert "renderGitBranchBadge" not in recent_match.group("body")

    quick_jump_match = re.search(
        r"function _buildQuickJumpItems\(\) \{(?P<body>.*?)\n\}",
        source, re.S,
    )
    assert quick_jump_match is not None
    assert "renderGitBranchBadge" not in quick_jump_match.group("body")
