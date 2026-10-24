"""Shared fixtures. Tests are offline: fake ollama via httpx.MockTransport, a local
recording HTTP server, and headless Chromium (skipped locally if it can't start; CI sets
UI_REVIEW_REQUIRE_BROWSER=1 so a missing browser fails instead)."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from collections.abc import Iterator
from dataclasses import replace
from functools import partial
from http.server import SimpleHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from ui_review.config import Config

SITE = Path(__file__).resolve().parent.parent / "examples" / "site"

for _var in ("OLLAMA_HOST", "UI_REVIEW_VISION", "UI_REVIEW_EXPLORE_STEPS", "UI_REVIEW_CDP_URL"):
    os.environ.pop(_var, None)
os.environ["UI_REVIEW_VISION"] = "off"


class RecordingServer:
    """Serves a directory and records every request line (method, path), any method."""

    def __init__(self, root: Path) -> None:
        self.requests: list[tuple[str, str]] = []
        log = self.requests

        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def parse_request(self) -> bool:
                ok = super().parse_request()
                if ok:
                    log.append((self.command, self.path))
                return ok

            def _refuse(self) -> None:
                self.send_error(405)

            do_POST = do_PUT = do_DELETE = do_PATCH = _refuse

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host!s}:{port}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def recording_server() -> Iterator[Callable[[Path], RecordingServer]]:
    servers: list[RecordingServer] = []

    def start(root: Path) -> RecordingServer:
        server = RecordingServer(root)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


def base_config(**changes: Any) -> Config:
    cfg = Config.from_env()
    return replace(cfg, **changes).validated()


@pytest.fixture(scope="session")
def browser_ok() -> bool:
    """Can Playwright start Chromium here? Skip browser tests if not (fail on CI)."""
    from playwright.sync_api import sync_playwright

    cfg = Config.from_env()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=cfg.chromium_path, args=list(cfg.chromium_args)
            )
            browser.close()
    except Exception as exc:
        if os.environ.get("UI_REVIEW_REQUIRE_BROWSER"):
            raise
        pytest.skip(f"Chromium is not available: {str(exc).splitlines()[0]}")
    return True


def fake_ollama(
    handler: Callable[[str, dict[str, Any]], dict[str, Any]],
    calls: list[tuple[str, dict[str, Any]]] | None = None,
) -> httpx.MockTransport:
    """An ollama stand-in: ``handler(path, json_body) -> json_response``."""

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        if calls is not None:
            calls.append((request.url.path, body))
        return httpx.Response(200, json=handler(request.url.path, body))

    return httpx.MockTransport(respond)
