from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "coderfleet" / "server" / "static"
INDEX_HTML = STATIC_DIR / "index.html"
PROJECTS_JS = STATIC_DIR / "js" / "projects.js"


def read_index() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


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


def test_log_modal_uses_neutral_color_tokens() -> None:
    source = read_ui_source()

    for token in ["--log-bg", "--log-panel", "--log-card", "--log-card-soft"]:
        assert token in source
