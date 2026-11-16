"""Plaid Link without Tk dialogs."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from security.link_server import open_in_chrome, stop_stale_servers
from settings import PROJECT_ROOT, link_server_info_path


def start_plaid_connect(*, timeout_sec: float = 20.0) -> dict:
    """Start link server and open Plaid in Chrome. Returns status dict."""
    try:
        stop_stale_servers()
        subprocess.Popen(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "setup_plaid.py"), "link", "--no-browser"],
            cwd=str(PROJECT_ROOT),
            start_new_session=True,
        )
    except Exception as exc:
        return {"ok": False, "message": str(exc)}

    deadline = time.monotonic() + timeout_sec
    info_path = link_server_info_path()
    while time.monotonic() < deadline:
        if info_path.exists():
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
                url = info["url"]
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        open_in_chrome(url)
                        return {
                            "ok": True,
                            "message": f"Plaid Link opened in Chrome. Complete linking there, then refresh balances.",
                            "url": url,
                        }
            except Exception:
                pass
        time.sleep(0.2)

    return {"ok": False, "message": "Plaid Link server did not become ready in time."}