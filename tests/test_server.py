from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ui_review.server import StaticServer


def fetch(url: str, method: str = "GET") -> tuple[int, bytes]:
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, b""


@pytest.fixture
def site(tmp_path: Path) -> Path:
    root = tmp_path / "site"
    (root / "docs").mkdir(parents=True)
    (root / "index.html").write_text("<h1>home</h1>")
    (root / "docs" / "index.html").write_text("<h1>docs</h1>")
    (root / "empty").mkdir()
    (root / ".env").write_text("SECRET=1")
    (tmp_path / "outside.txt").write_text("outside")
    os.symlink(tmp_path / "outside.txt", root / "link.txt")
    return root


def test_serves_files_and_indexes(site: Path) -> None:
    with StaticServer(site) as server:
        assert server.url.startswith("http://127.0.0.1:")
        assert fetch(server.url + "/") == (200, b"<h1>home</h1>")
        assert fetch(server.url + "/docs/") == (200, b"<h1>docs</h1>")
        assert fetch(server.url + "/index.html?x=1")[0] == 200


@pytest.mark.parametrize(
    "path",
    [
        "/.env",
        "/link.txt",
        "/empty/",
        "/missing.html",
        "/%2e%2e/outside.txt",
        "/docs/../../outside.txt",
    ],
)
def test_refuses_escapes_dotfiles_and_listings(site: Path, path: str) -> None:
    with StaticServer(site) as server:
        assert fetch(server.url + path)[0] == 404


def test_only_get_and_head(site: Path) -> None:
    with StaticServer(site) as server:
        assert fetch(server.url + "/", "HEAD")[0] == 200
        assert fetch(server.url + "/", "POST")[0] == 501
