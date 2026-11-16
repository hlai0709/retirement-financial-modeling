#!/usr/bin/env python3
"""
Launch Plaid Link in Chrome via a local-only HTTP server.

Usage:
    python scripts/setup_plaid.py link
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import urlparse

from services.data_service import DataService
from providers.plaid_provider import ExchangeResult, PlaidProvider, default_link_user_id
from security.link_server import (
    LINK_PORT,
    clear_link_server_info,
    open_in_chrome,
    require_port,
    stop_stale_servers,
)
from settings import get_settings, link_server_info_path

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = LINK_PORT
LINK_TIMEOUT_SECONDS = 600


def _link_html(_link_token_unused: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Connect Accounts — Retirement Planner</title>
  <script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, sans-serif;
      max-width: 36rem;
      margin: 3rem auto;
      padding: 0 1rem;
      color: #1d1d1f;
    }}
    h1 {{ font-size: 1.4rem; }}
    p {{ color: #555; line-height: 1.5; }}
    #status {{ margin-top: 1.5rem; }}
    .error {{ color: #b00020; }}
    .success {{ color: #0a7a2f; }}
  </style>
</head>
<body>
  <h1>Connect a financial institution</h1>
  <p>Plaid Link will open so you can sign in securely. Credentials are entered
     in Plaid&apos;s UI — never in this app.</p>
  <p>Plaid Link opens automatically in Chrome. Use the button below only if it does not.</p>
  <div id="status">Preparing Plaid Link…</div>
  <p><button id="open-btn" type="button" disabled>Open Plaid Link</button></p>
  <script>
    const statusEl = document.getElementById("status");
    const openBtn = document.getElementById("open-btn");
    let handler = null;

    function setStatus(message, className) {{
      statusEl.textContent = message;
      statusEl.className = className || "";
    }}

    async function getLinkToken() {{
      const response = await fetch("/api/link_token");
      const data = await response.json();
      if (!response.ok) {{
        throw new Error(data.error || "Could not create link token");
      }}
      return data.link_token;
    }}

    function buildHandler(token) {{
      return Plaid.create({{
        token: token,
        onSuccess: async (public_token, metadata) => {{
          setStatus("Finishing connection…");
          try {{
            const response = await fetch("/exchange", {{
              method: "POST",
              headers: {{ "Content-Type": "application/json" }},
              body: JSON.stringify({{ public_token, metadata }}),
            }});
            const data = await response.json();
            if (!response.ok) {{
              throw new Error(data.error || "Token exchange failed");
            }}
            setStatus(
              "Connected to " + (data.institution_name || "institution") +
              ". You can close this tab and return to the app.",
              "success"
            );
          }} catch (err) {{
            setStatus("Error: " + err.message, "error");
          }}
        }},
        onExit: (err, metadata) => {{
          if (err != null) {{
            setStatus(
              "Link closed: " + (err.display_message || err.error_message || err.error_code || "unknown error"),
              "error"
            );
          }} else {{
            setStatus("Link closed before completing connection.", "error");
          }}
          openBtn.disabled = false;
        }},
      }});
    }}

    async function startLink() {{
      if (typeof Plaid === "undefined") {{
        setStatus(
          "Could not load Plaid script (cdn.plaid.com). Check your internet connection and refresh.",
          "error"
        );
        return;
      }}
      try {{
        setStatus("Creating link session…");
        const token = await getLinkToken();
        handler = buildHandler(token);
        openBtn.disabled = false;
        openBtn.onclick = () => handler.open();
        setStatus("Plaid Link ready — opening now…");
        handler.open();
      }} catch (err) {{
        setStatus("Error: " + err.message, "error");
      }}
    }}

    window.addEventListener("load", startLink);
  </script>
</body>
</html>"""


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class _PlaidLinkHandler(BaseHTTPRequestHandler):
    """Serve Link page and accept the public_token callback."""

    link_token: str = ""
    provider: PlaidProvider | None = None
    data_service: DataService | None = None
    completion_event: threading.Event | None = None
    exchange_result: ExchangeResult | None = None
    exchange_error: str | None = None

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/link_token":
            try:
                assert self.provider is not None
                link = self.provider.create_link_token(default_link_user_id())
                self._send_json(200, {"link_token": link.link_token})
            except Exception as exc:
                logger.exception("link_token_create failed")
                self._send_json(500, {"error": str(exc)})
            return
        if path in ("/", "/index.html"):
            body = _link_html("").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/exchange":
            self.send_error(404, "Not found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
            public_token = payload.get("public_token", "")
            if not public_token:
                raise ValueError("public_token missing from callback")

            assert self.provider is not None
            assert self.data_service is not None

            result = self.provider.exchange_public_token(public_token)
            config = self.data_service.sync_account_mappings(result.item_id)

            item_info = self.provider.get_item(result.item_id)
            institution_name = item_info.get("item", {}).get("institution_name", "Unknown")

            self.exchange_result = result
            response_body = {
                "item_id": result.item_id,
                "institution_name": institution_name,
                "account_count": sum(
                    1 for mapping in config.account_mappings if mapping.item_id == result.item_id
                ),
            }
            self._send_json(200, response_body)
            if self.completion_event:
                self.completion_event.set()
        except Exception as exc:
            logger.exception("Plaid exchange failed")
            self.exchange_error = str(exc)
            self._send_json(500, {"error": str(exc)})
            if self.completion_event:
                self.completion_event.set()

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("setup_plaid: " + format, *args)


def _browser_url(host: str, port: int) -> str:
    display_host = "127.0.0.1" if host in ("127.0.0.1", "localhost", "::1") else host
    return f"http://{display_host}:{port}/"


def _write_link_server_info(url: str, port: int) -> None:
    info_path = link_server_info_path()
    info_path.parent.mkdir(parents=True, exist_ok=True)
    info_path.write_text(
        json.dumps({"url": url, "port": port, "pid": os.getpid()}),
        encoding="utf-8",
    )


def _wait_until_ready(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.15)
    raise TimeoutError(f"Link server did not become ready at {url}")


def run_link_flow(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    open_browser: bool = True,
) -> ExchangeResult:
    """Start local server, open browser, and complete Plaid Link."""
    settings = get_settings(require_plaid=True)
    provider = PlaidProvider(settings)
    data_service = DataService(plaid=provider)

    port = require_port(host, port)
    completion = threading.Event()

    handler_cls = _PlaidLinkHandler
    handler_cls.link_token = ""
    handler_cls.provider = provider
    handler_cls.data_service = data_service
    handler_cls.completion_event = completion
    handler_cls.exchange_result = None
    handler_cls.exchange_error = None

    server = _ThreadingHTTPServer((host, port), handler_cls)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    url = _browser_url(host, port)
    _write_link_server_info(url, port)
    _wait_until_ready(url)
    print(f"Plaid Link server ready at {url}")
    print("Complete sign-in in Chrome, then click Open Plaid Link on the page. Press Ctrl+C to cancel.")
    if open_browser:
        open_in_chrome(url)
        print(f"Opened in Chrome: {url}")

    try:
        completed = completion.wait(timeout=LINK_TIMEOUT_SECONDS)
    finally:
        server.shutdown()
        server_thread.join(timeout=5)
        clear_link_server_info()

    if not completed:
        raise TimeoutError(f"Plaid Link timed out after {LINK_TIMEOUT_SECONDS} seconds")

    if handler_cls.exchange_error:
        raise RuntimeError(handler_cls.exchange_error)

    if handler_cls.exchange_result is None:
        raise RuntimeError("Plaid Link closed without completing connection")

    result = handler_cls.exchange_result
    item_info = provider.get_item(result.item_id)
    institution_name = item_info.get("item", {}).get("institution_name", "Unknown")
    print(f"Connected: {institution_name} (item_id={result.item_id})")
    print("Access token saved to macOS Keychain.")
    print("Run `python scripts/list_accounts.py` to review accounts and map owners.")
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plaid Link setup helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    link_parser = subparsers.add_parser("link", help="Connect a new institution via Plaid Link")
    link_parser.add_argument("--host", default=DEFAULT_HOST, help="Local bind host")
    link_parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Local bind port")
    link_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Start server only; caller opens the browser when ready",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _build_parser().parse_args(argv)

    if args.command == "link":
        try:
            if not args.no_browser:
                stop_stale_servers()
            run_link_flow(host=args.host, port=args.port, open_browser=not args.no_browser)
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())