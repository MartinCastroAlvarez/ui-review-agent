"""Serve a local static build on loopback so it can be reviewed like a deployed site."""

from __future__ import annotations

import threading
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import unquote
from urllib.parse import urlsplit


class _Handler(SimpleHTTPRequestHandler):
    """GET/HEAD only, no directory listings, no dotfiles, nothing outside the root."""

    root: Path

    def __init__(self, *args: Any, root: Path, **kwargs: Any) -> None:
        self.root = root
        super().__init__(*args, directory=str(root), **kwargs)

    def translate_path(self, path: str) -> str:
        rel = unquote(urlsplit(path).path).lstrip("/")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if any(p == ".." or p.startswith(".") for p in parts):
            return str(self.root / ".refused")
        candidate = self.root.joinpath(*parts)
        try:
            resolved = candidate.resolve()
        except OSError:
            return str(self.root / ".refused")
        if resolved != self.root and self.root not in resolved.parents:
            return str(self.root / ".refused")
        if resolved.is_dir():
            index = resolved / "index.html"
            return str(index if index.is_file() else self.root / ".refused")
        return str(resolved)

    def list_directory(self, path: str | Any) -> None:  # pragma: no cover - never reached
        self.send_error(HTTPStatus.NOT_FOUND)
        return

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


class StaticServer:
    """``with StaticServer(root) as server: server.url`` -> ``http://127.0.0.1:<port>``."""

    def __init__(self, root: Path, host: str = "127.0.0.1", port: int = 0) -> None:
        self.root = root.resolve()
        handler = partial(_Handler, root=self.root)
        self._httpd = ThreadingHTTPServer((host, port), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host!s}:{port}"

    def start(self) -> StaticServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> StaticServer:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()
