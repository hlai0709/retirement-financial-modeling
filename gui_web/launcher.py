"""Launch Retirement Planner with approved HTML/CSS mockups (pywebview)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import webview

from app.bundle_paths import is_frozen, mockups_dir, project_root
from gui.version import APP_BUILD_LABEL
from gui_web.api import WebAppApi

_MOCKUPS_DIR = mockups_dir()
_LIVE_APP = _MOCKUPS_DIR / "live-app.html"
_LOG_PATH = Path.home() / "Library" / "Application Support" / "RetirementPlanner" / "launcher.log"


def _log(message: str) -> None:
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except OSError:
        pass


def _reexec_process() -> None:
    """Replace this process with a fresh app launch (picks up code + config)."""
    if is_frozen():
        # py2app / frozen binary: re-exec the bundled executable
        os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
    python = sys.executable
    os.execv(python, [python, *sys.argv])


def run_web_app() -> None:
    _log("run_web_app: starting")
    if not _LIVE_APP.is_file():
        raise FileNotFoundError(
            f"Missing web shell: {_LIVE_APP} (root={project_root()}, frozen={is_frozen()})"
        )

    _log(f"run_web_app: build={APP_BUILD_LABEL} frozen={is_frozen()} root={project_root()}")

    _log(f"run_web_app: loading {_LIVE_APP.name}")
    api = WebAppApi()
    webview.create_window(
        "Retirement Planner",
        str(_LIVE_APP),
        width=1020,
        height=820,
        min_size=(900, 700),
        resizable=True,
        js_api=api,
    )
    # http_server serves sibling assets (css, js) from design/mockups/
    _log("run_web_app: opening window")
    try:
        # Keep debug off by default — it auto-opens Web Inspector and can cover window controls.
        debug = os.environ.get("RETIREMENT_PLANNER_WEB_DEBUG") == "1"
        webview.start(http_server=True, gui="cocoa", debug=debug)
    finally:
        _log("run_web_app: window closed")

    if getattr(api, "restart_requested", False):
        _log("run_web_app: restart requested — re-exec")
        _reexec_process()
    _log("run_web_app: exited")
