from __future__ import annotations

"""
System-tray / menu-bar app for CoderFleet.

  macOS   — rumps  (native menu-bar, AppKit notifications)
  Windows — pystray + pillow + plyer (system tray, Toast notifications)
  Linux   — pystray + pillow + notify-send (system tray, libnotify)

Architecture
────────────
The tray does NOT own the server as a subprocess.
Server lifecycle is managed via PID file (server_daemon module),
same as `coderfleet server --daemon`.  Quitting the tray leaves
the server running.

On macOS, LaunchAgent (installed via `coderfleet tray install`) starts this
process at login.  launchd handles crash-restart.
"""

import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from coderfleet.config import get_workspace, load_config
from coderfleet import server_daemon as sd


def _load_api_key(ws: Path) -> str:
    key_file = ws / "api_key.txt"
    if key_file.exists():
        return key_file.read_text().strip()
    return ""

_POLL_INTERVAL = 5   # seconds between task polls / server-health checks
_LAUNCHAGENT_LABEL = "com.coderfleet.tray"
_LAUNCHAGENT_PLIST = (
    Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHAGENT_LABEL}.plist"
)


# ── notifications ──────────────────────────────────────────────────────────────

_ICON_PATH = Path(__file__).parent / "server" / "static" / "icons" / "logo-mark.png"


def _notify(title: str, message: str) -> None:
    """Send a native desktop notification (cross-platform)."""
    if sys.platform == "darwin":
        _notify_macos(title, message)
    elif sys.platform == "win32":
        _notify_windows(title, message)
    else:
        _notify_linux(title, message)


def _notify_macos(title: str, message: str) -> None:
    _title = title.replace('"', '\\"')
    _msg   = message.replace('"', '\\"')
    script = (
        f'display notification "{_msg}" with title "{_title}" '
        f'sound name "default"'
    )
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=3)
    except Exception:
        pass

    try:
        from Foundation import NSUserNotification, NSUserNotificationCenter
        import AppKit
        n = NSUserNotification.alloc().init()
        n.setTitle_(title)
        n.setInformativeText_(message)
        if _ICON_PATH.exists():
            img = AppKit.NSImage.alloc().initWithContentsOfFile_(str(_ICON_PATH))
            if img:
                n.setContentImage_(img)
        NSUserNotificationCenter.defaultUserNotificationCenter().deliverNotification_(n)
    except Exception:
        pass


def _notify_windows(title: str, message: str) -> None:
    try:
        from plyer import notification as plyer_notif
        plyer_notif.notify(
            title=title, message=message,
            app_name="CoderFleet",
            app_icon=str(_ICON_PATH) if _ICON_PATH.exists() else None,
            timeout=5,
        )
    except Exception:
        pass


def _notify_linux(title: str, message: str) -> None:
    try:
        subprocess.run(
            ["notify-send", "--app-name=CoderFleet", title, message],
            capture_output=True, timeout=3,
        )
    except Exception:
        try:
            from plyer import notification as plyer_notif
            plyer_notif.notify(title=title, message=message, app_name="CoderFleet", timeout=5)
        except Exception:
            pass


# ── LaunchAgent management ─────────────────────────────────────────────────────

def _plist_content(python_exe: str, ws: str, current_path: str) -> str:
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_LAUNCHAGENT_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{python_exe}</string>
        <string>-m</string>
        <string>coderfleet</string>
        <string>tray</string>
    </array>

    <!-- Restart on crash, but not on clean exit (user quit via menu). -->
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <!-- Required for menu-bar / GUI processes. -->
    <key>ProcessType</key>
    <string>Interactive</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{current_path}</string>
        <key>CODERFLEET_WORKSPACE</key>
        <string>{ws}</string>
    </dict>

    <key>StandardOutPath</key>
    <string>{Path.home()}/.coderfleet/tray.log</string>
    <key>StandardErrorPath</key>
    <string>{Path.home()}/.coderfleet/tray.log</string>
</dict>
</plist>
"""


def install_launchagent() -> None:
    import os
    ws = get_workspace()
    _LAUNCHAGENT_PLIST.parent.mkdir(parents=True, exist_ok=True)
    _LAUNCHAGENT_PLIST.write_text(
        _plist_content(
            python_exe=sys.executable,
            ws=str(ws),
            current_path=os.environ.get("PATH", "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"),
        )
    )
    subprocess.run(["launchctl", "load", str(_LAUNCHAGENT_PLIST)], check=False)


def uninstall_launchagent() -> None:
    if _LAUNCHAGENT_PLIST.exists():
        subprocess.run(["launchctl", "unload", str(_LAUNCHAGENT_PLIST)], check=False)
        _LAUNCHAGENT_PLIST.unlink()


def launchagent_installed() -> bool:
    return _LAUNCHAGENT_PLIST.exists()


def launchagent_ctl(action: str) -> None:
    """action: 'start' or 'stop'"""
    subprocess.run(["launchctl", action, _LAUNCHAGENT_LABEL], check=False)


# ── rumps tray app ─────────────────────────────────────────────────────────────

def _run_macos(ws, port: int) -> None:
    import rumps

    _api_key = _load_api_key(ws)
    _headers = {"Authorization": f"Bearer {_api_key}"} if _api_key else {}

    def _current_port() -> int:
        return int(load_config(ws).get("CODERFLEET_PORT", port))

    class _App(rumps.App):
        def __init__(self_inner) -> None:
            self_inner._seen: set[str] = set()
            self_inner._user_stopped: bool = False
            self_inner._status_item = rumps.MenuItem("Server: …")

            icon_path = (
                Path(__file__).parent / "server" / "static" / "icons" / "logo-mark.png"
            )
            super().__init__(
                "CoderFleet",
                icon=str(icon_path) if icon_path.exists() else None,
                menu=[
                    self_inner._status_item,
                    None,
                    rumps.MenuItem("Open Web UI", callback=self_inner._open_ui),
                    None,
                    rumps.MenuItem("Start Server", callback=self_inner._start_server),
                    rumps.MenuItem("Stop Server", callback=self_inner._stop_server),
                    None,
                    rumps.MenuItem("Check for Updates", callback=self_inner._check_update),
                    None,
                    rumps.MenuItem("Quit", callback=self_inner._quit),
                ],
                quit_button=None,
            )

        # ── actions ──────────────────────────────────────────────

        def _open_ui(self_inner, _) -> None:
            webbrowser.open(f"http://localhost:{_current_port()}")

        def _start_server(self_inner, _) -> None:
            threading.Thread(target=self_inner._do_start, daemon=True).start()

        def _do_start(self_inner) -> None:
            self_inner._user_stopped = False
            try:
                new_pid = sd.start_daemon(ws, _current_port())
                self_inner._status_item.title = f"Server: Running  (PID {new_pid})"
                _notify("CoderFleet", f"Server started (PID {new_pid})")
            except Exception as exc:
                _notify("CoderFleet", f"Failed to start server: {exc}")

        def _stop_server(self_inner, _) -> None:
            threading.Thread(target=self_inner._do_stop, daemon=True).start()

        def _do_stop(self_inner) -> None:
            self_inner._user_stopped = True
            stopped = sd.stop_daemon(ws)
            if stopped:
                self_inner._status_item.title = "Server: Stopped"
                _notify("CoderFleet", "Server stopped")
            else:
                _notify("CoderFleet", "Server was not running")

        def _quit(self_inner, _) -> None:
            # Server keeps running after tray quits.
            rumps.quit_application()

        def _check_update(self_inner, _) -> None:
            threading.Thread(target=self_inner._do_check_update, daemon=True).start()

        def _do_check_update(self_inner) -> None:
            try:
                import httpx
                from importlib.metadata import version as pkg_version
                current = pkg_version("coderfleet")
                resp = httpx.get("https://pypi.org/pypi/coderfleet/json", timeout=8)
                latest = resp.json()["info"]["version"]
                if latest != current:
                    _notify(
                        "CoderFleet Update Available",
                        f"{current} → {latest}  |  pip install --upgrade coderfleet",
                    )
                else:
                    _notify("CoderFleet", f"Already up to date ({current})")
            except Exception as exc:
                _notify("CoderFleet", f"Update check failed: {exc}")

        # ── watchdog timer ────────────────────────────────────────

        @rumps.timer(_POLL_INTERVAL)
        def _watchdog(self_inner, _) -> None:
            # Refresh server status label.
            pid = sd.read_pid(ws)
            if pid and sd.is_running(pid):
                self_inner._status_item.title = f"Server: Running  (PID {pid})"
            else:
                sd.pid_file(ws).unlink(missing_ok=True)
                self_inner._status_item.title = "Server: Stopped"
                # Only auto-restart on unexpected crash, not when user stopped it.
                if not self_inner._user_stopped:
                    try:
                        new_pid = sd.start_daemon(ws, _current_port())
                        self_inner._status_item.title = f"Server: Running  (PID {new_pid})"
                        _notify("CoderFleet", f"Server restarted (PID {new_pid})")
                    except Exception:
                        pass

            # Task-completion notifications.
            self_inner._poll_tasks()

        def _poll_tasks(self_inner) -> None:
            try:
                import httpx
                resp = httpx.get(
                    f"http://localhost:{_current_port()}/api/tasks",
                    headers=_headers,
                    timeout=2,
                )
                if resp.status_code != 200:
                    return
                tasks = resp.json()
            except Exception:
                return

            for task in tasks:
                tid = task.get("id", "")
                status = task.get("status", "")
                if status not in {"done", "failed", "killed"} or tid in self_inner._seen:
                    continue
                self_inner._seen.add(tid)
                label = task.get("project_name") or task.get("project", "?")
                if status == "done":
                    _notify("CoderFleet: Task Done", f"{label} — completed")
                else:
                    _notify(f"CoderFleet: Task {status.capitalize()}", label)

    # ── startup ───────────────────────────────────────────────────

    app = _App()

    # Ensure server is running before entering the run loop.
    if sd.server_is_running(ws):
        pid = sd.read_pid(ws)
        app._status_item.title = f"Server: Running  (PID {pid})"
    else:
        try:
            new_pid = sd.start_daemon(ws, _current_port())
            app._status_item.title = f"Server: Running  (PID {new_pid})"
        except Exception as exc:
            app._status_item.title = "Server: Failed to start"
            _notify("CoderFleet", f"Could not start server: {exc}")

    # Pre-populate _seen with all already-terminal tasks so the first
    # watchdog tick doesn't fire notifications for historical tasks.
    try:
        import httpx, time
        time.sleep(1.5)  # give server a moment to be ready
        resp = httpx.get(
            f"http://localhost:{_current_port()}/api/tasks",
            headers=_headers,
            timeout=3,
        )
        if resp.status_code == 200:
            for t in resp.json():
                if t.get("status") in {"done", "failed", "killed"}:
                    app._seen.add(t.get("id", ""))
    except Exception:
        pass

    # Set bundle identifier and app icon so macOS associates notifications
    # with CoderFleet rather than the generic Python interpreter bundle.
    if _ICON_PATH.exists():
        try:
            import AppKit
            ns_app = AppKit.NSApplication.sharedApplication()
            img = AppKit.NSImage.alloc().initWithContentsOfFile_(str(_ICON_PATH))
            if img:
                ns_app.setApplicationIconImage_(img)
            # Override the bundle identifier at runtime so the notification
            # centre groups our notifications under "CoderFleet".
            info = AppKit.NSBundle.mainBundle().infoDictionary()
            if info is not None:
                info["CFBundleIdentifier"] = "com.coderfleet.tray"
                info["CFBundleName"] = "CoderFleet"
        except Exception:
            pass

    app.run()


# ── pystray tray app (Windows / Linux) ────────────────────────────────────────

def _run_pystray(ws: Path, port: int) -> None:
    import pystray
    from PIL import Image as _Image

    _api_key = _load_api_key(ws)
    _headers = {"Authorization": f"Bearer {_api_key}"} if _api_key else {}

    _status = {"label": "Server: …"}
    _seen: set[str] = set()
    _user_stopped = {"value": False}

    def _current_port() -> int:
        return int(load_config(ws).get("CODERFLEET_PORT", port))

    try:
        _img = _Image.open(_ICON_PATH)
    except Exception:
        _img = _Image.new("RGBA", (64, 64), (13, 148, 136, 255))

    # ── actions ──────────────────────────────────────────────────────

    def _open_ui(icon, item) -> None:
        webbrowser.open(f"http://localhost:{_current_port()}")

    def _start_server(icon, item) -> None:
        threading.Thread(target=_do_start, args=(icon,), daemon=True).start()

    def _do_start(icon) -> None:
        _user_stopped["value"] = False
        try:
            new_pid = sd.start_daemon(ws, _current_port())
            _status["label"] = f"Server: Running  (PID {new_pid})"
            icon.update_menu()
            _notify("CoderFleet", f"Server started (PID {new_pid})")
        except Exception as exc:
            _notify("CoderFleet", f"Failed to start server: {exc}")

    def _stop_server(icon, item) -> None:
        threading.Thread(target=_do_stop, args=(icon,), daemon=True).start()

    def _do_stop(icon) -> None:
        _user_stopped["value"] = True
        stopped = sd.stop_daemon(ws)
        _status["label"] = "Server: Stopped" if stopped else "Server: Not running"
        icon.update_menu()
        if stopped:
            _notify("CoderFleet", "Server stopped")

    def _check_update(icon, item) -> None:
        threading.Thread(target=_do_check_update, daemon=True).start()

    def _do_check_update() -> None:
        try:
            import httpx
            from importlib.metadata import version as pkg_version
            current = pkg_version("coderfleet")
            resp = httpx.get("https://pypi.org/pypi/coderfleet/json", timeout=8)
            latest = resp.json()["info"]["version"]
            if latest != current:
                _notify("CoderFleet Update Available",
                        f"{current} → {latest}  |  pip install --upgrade coderfleet")
            else:
                _notify("CoderFleet", f"Already up to date ({current})")
        except Exception as exc:
            _notify("CoderFleet", f"Update check failed: {exc}")

    def _quit(icon, item) -> None:
        icon.stop()

    # ── watchdog ─────────────────────────────────────────────────────

    def _watchdog(icon) -> None:
        while True:
            time.sleep(_POLL_INTERVAL)
            pid = sd.read_pid(ws)
            if pid and sd.is_running(pid):
                _status["label"] = f"Server: Running  (PID {pid})"
            else:
                sd.pid_file(ws).unlink(missing_ok=True)
                _status["label"] = "Server: Stopped"
                # Only auto-restart on unexpected crash, not when user stopped it.
                if not _user_stopped["value"]:
                    try:
                        new_pid = sd.start_daemon(ws, _current_port())
                        _status["label"] = f"Server: Running  (PID {new_pid})"
                        _notify("CoderFleet", f"Server restarted (PID {new_pid})")
                    except Exception:
                        pass
            icon.update_menu()
            _poll_tasks_pystray(icon)

    def _poll_tasks_pystray(icon) -> None:
        try:
            import httpx
            resp = httpx.get(
                f"http://localhost:{_current_port()}/api/tasks",
                headers=_headers,
                timeout=2,
            )
            if resp.status_code != 200:
                return
            tasks = resp.json()
        except Exception:
            return

        for task in tasks:
            tid    = task.get("id", "")
            status = task.get("status", "")
            if status not in {"done", "failed", "killed"} or tid in _seen:
                continue
            _seen.add(tid)
            label = task.get("project_name") or task.get("project", "?")
            if status == "done":
                _notify("CoderFleet: Task Done", f"{label} — completed")
            else:
                _notify(f"CoderFleet: Task {status.capitalize()}", label)

    def _setup(icon) -> None:
        icon.visible = True
        # Pre-populate _seen so the first watchdog tick skips historical tasks.
        try:
            import httpx
            time.sleep(1.5)
            resp = httpx.get(
                f"http://localhost:{_current_port()}/api/tasks",
                headers=_headers,
                timeout=3,
            )
            if resp.status_code == 200:
                for t in resp.json():
                    if t.get("status") in {"done", "failed", "killed"}:
                        _seen.add(t.get("id", ""))
        except Exception:
            pass
        threading.Thread(target=_watchdog, args=(icon,), daemon=True).start()

    # ── startup ───────────────────────────────────────────────────────

    if sd.server_is_running(ws):
        pid = sd.read_pid(ws)
        _status["label"] = f"Server: Running  (PID {pid})"
    else:
        try:
            new_pid = sd.start_daemon(ws, _current_port())
            _status["label"] = f"Server: Running  (PID {new_pid})"
        except Exception as exc:
            _status["label"] = "Server: Failed to start"
            _notify("CoderFleet", f"Could not start server: {exc}")

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: _status["label"], lambda icon, item: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open Web UI", _open_ui),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start Server", _start_server),
        pystray.MenuItem("Stop Server", _stop_server),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Check for Updates", _check_update),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _quit),
    )

    icon = pystray.Icon("CoderFleet", _img, "CoderFleet", menu)
    icon.run(setup=_setup)


# ── entry points ───────────────────────────────────────────────────────────────

def run_tray() -> None:
    ws = get_workspace()
    cfg = load_config(ws)
    port = int(cfg.get("CODERFLEET_PORT", 8765))
    if sys.platform == "darwin":
        _run_macos(ws, port)
    else:
        _run_pystray(ws, port)
