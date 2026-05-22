from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "server" / "static" / "index.html"
PROJECTS_JS = ROOT / "server" / "static" / "js" / "projects.js"


def read_index() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def read_projects_js() -> str:
    return PROJECTS_JS.read_text(encoding="utf-8")


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

    assert 'id="task-submit-panel"' in html
    assert 'id="task-submit-slot"' in html
    assert 'id="submit-modal"' in html
    assert 'id="submit-modal-slot"' in html
    assert "function openTaskSubmitPanel" in html
    assert "function openProjectSubmitModal" in html
    assert "function moveSubmitPanel" in html
    assert "function closeTaskSubmitPanel" in html
    assert "function closeSubmitModal" in html
    assert "submitContext" in html
    assert "submit-panel-close-btn" in html
    assert "populateConversations(convs, tasks, submitContext.projectName)" in html
    assert "populateProjects(projects, submitContext.projectName)" in html
    assert 'data-page="submit"' not in html
    assert 'id="page-submit"' not in html


def test_task_queue_uses_time_sorted_pagination_without_global_chain_board() -> None:
    html = read_index()

    assert 'id="conversation-grid"' not in html
    assert "function renderConversationBoard" not in html
    assert 'id="task-pagination"' in html
    assert "TASK_PAGE_SIZE" in html
    assert "function setTaskPage" in html
    assert "new Date(b.created || 0) - new Date(a.created || 0)" in html
    assert "任务链视图" not in html


def test_dashboard_exposes_project_workspace() -> None:
    html = read_index()

    assert 'data-page="projects"' in html
    assert 'id="project-grid"' in html
    assert 'id="project-detail-view"' in html
    assert 'data-project-filter="all"' not in html
    assert 'data-project-filter="chains"' in html
    assert 'data-project-filter="one-off"' in html
    assert 'id="project-status-filter"' not in html
    assert 'data-project-status="running"' in html
    assert 'data-project-status="done"' in html
    assert 'data-project-status="failed"' in html
    assert 'id="project-search"' in html
    assert "project-chain-workspace" in html
    assert "project-chain-area" in html
    assert "project-chain-detail-tasks" in html
    assert "function loadProjectsDashboard" in html
    assert "function renderProjectDetail" in html
    assert "function setProjectTaskFilter" in html
    assert "function setProjectStatusFilter" in html
    assert "function getProjectFilteredTasks" in html
    assert "function chooseProjectTaskFilter" in html
    assert "function selectProjectConversation" in html
    assert "function renderProjectTaskCard" in html


def test_legacy_project_records_use_single_canonical_path_owner() -> None:
    js = read_projects_js()

    assert "function canonicalProjectForLegacyRecord" in js
    assert "if (task.project_name) return task.project_name === project.name;" in js
    assert "if (conversation.project_name) return conversation.project_name === project.name;" in js
    assert "if (canonical) return canonical.name === project.name;" in js


def test_project_workspace_exposes_embedded_terminal() -> None:
    html = read_index()

    assert '/static/vendor/xterm/xterm.css' in html
    assert '/static/vendor/xterm/xterm.js' in html
    assert '/static/vendor/xterm/addon-fit.js' in html
    assert 'data-project-panel="tasks"' in html
    assert 'data-project-panel="terminal"' in html
    assert 'id="project-terminal-panel"' in html
    assert 'id="project-terminal-status"' in html
    assert 'id="project-terminal"' in html
    assert 'id="project-terminal-reconnect"' in html
    assert "terminalContext" in html
    assert "function openProjectTerminal" in html
    assert "function connectProjectTerminal" in html
    assert "function disconnectProjectTerminal" in html
    assert "function resizeProjectTerminal" in html
    assert "window.addEventListener('beforeunload', disconnectProjectTerminal)" in html
    assert "disconnectProjectTerminal();" in html


def test_sidebar_can_collapse_with_persisted_state() -> None:
    html = read_index()

    assert 'id="sidebar-toggle"' in html
    assert 'aria-label="折叠侧边栏"' in html
    assert 'aria-expanded="true"' in html
    assert "function toggleSidebar" in html
    assert "function applySidebarCollapsed" in html
    assert "localStorage.getItem('aicm.sidebarCollapsed')" in html
    assert "localStorage.setItem('aicm.sidebarCollapsed'" in html
    assert "sidebar-collapsed" in html
    assert "sidebar-label" in html
    assert "brand-text" in html
    assert "sidebar-toggle-icon" in html


def test_account_cards_use_structured_resource_layout() -> None:
    html = read_index()

    for class_name in [
        "account-card-head",
        "account-stats",
        "chip-list",
        "container-list",
    ]:
        assert class_name in html


def test_tool_log_icons_are_text_tokens_not_emoji() -> None:
    html = read_index()
    match = re.search(r"const TOOL_ICONS = \{(?P<body>.*?)\};", html, re.S)

    assert match is not None
    assert not re.search(r"[\U0001F300-\U0001FAFF]", match.group("body"))


def test_icon_only_controls_have_accessible_names() -> None:
    html = read_index()

    assert 'id="follow-btn"' in html
    assert 'aria-label="切换日志跟踪"' in html
    assert 'aria-label="关闭日志"' in html


def test_log_modal_has_summary_and_timeline_shell() -> None:
    html = read_index()

    for element_id in [
        "log-summary-status",
        "log-summary-account",
        "log-summary-project",
        "log-summary-created",
    ]:
        assert f'id="{element_id}"' in html

    assert 'class="log-shell"' in html
    assert 'class="log-summary"' in html
    assert "chat-log timeline" in html


def test_log_modal_uses_neutral_color_tokens() -> None:
    html = read_index()

    for token in ["--log-bg", "--log-panel", "--log-card", "--log-card-soft"]:
        assert token in html
