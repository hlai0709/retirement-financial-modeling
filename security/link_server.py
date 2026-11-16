"""Shared helpers for the local Plaid Link HTTP server."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

from settings import PROJECT_ROOT, link_server_info_path

_SETUP_PLAID_MARKER = str(PROJECT_ROOT / "scripts" / "setup_plaid.py")

LINK_PORT = 8765


def clear_link_server_info() -> None:
    info_path = link_server_info_path()
    if info_path.exists():
        info_path.unlink()


def _kill_listeners_on_port(port: int) -> None:
    result = subprocess.run(
        ["lsof", "-ti", f":{port}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return
    for pid in result.stdout.split():
        subprocess.run(["kill", "-9", pid], check=False)


def stop_stale_servers(wait_seconds: float = 3.0) -> None:
    """Terminate leftover link-server processes and wait for the port to free."""
    subprocess.run(["pkill", "-f", _SETUP_PLAID_MARKER], check=False)
    _kill_listeners_on_port(LINK_PORT)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        running = subprocess.run(
            ["pgrep", "-f", _SETUP_PLAID_MARKER],
            capture_output=True,
            text=True,
        )
        port_probe = subprocess.run(
            ["lsof", "-ti", f":{LINK_PORT}"],
            capture_output=True,
            text=True,
        )
        if running.returncode != 0 and port_probe.returncode != 0:
            break
        time.sleep(0.2)
    clear_link_server_info()


def require_port(host: str, port: int = LINK_PORT) -> int:
    """Return port when available; raise if another process still owns it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            raise RuntimeError(
                f"Port {port} is still in use. Click Connect Accounts again in a few seconds."
            ) from exc
    return port


def open_in_chrome(url: str) -> None:
    """Open the link page in Google Chrome."""
    if sys.platform == "darwin":
        for app_name in ("Google Chrome", "Google Chrome Canary", "Chromium"):
            app_path = Path("/Applications") / f"{app_name}.app"
            if app_path.is_dir():
                subprocess.run(["open", "-a", app_name, url], check=False)
                return
    import webbrowser

    webbrowser.open(url)