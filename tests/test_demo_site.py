"""The agent must find every deliberate bug in examples/site, and nothing it shouldn't."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Iterator

import pytest

from tests.conftest import SITE
from tests.conftest import RecordingServer
from tests.conftest import base_config
from ui_review.findings import Finding
from ui_review.findings import Severity
from ui_review.report import write_reports
from ui_review.review import ReviewResult
from ui_review.review import review

pytestmark = pytest.mark.browser
ALL_VIEWS = {f"{v}-{s}" for v in ("mobile", "tablet", "desktop") for s in ("light", "dark")}


@pytest.fixture(scope="module")
def demo(
    browser_ok: bool, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[ReviewResult, RecordingServer]]:
    server = RecordingServer(SITE)
    out = tmp_path_factory.mktemp("report")
    try:
        result = review(base_config(target=server.url + "/", out_dir=out))
        write_reports(result)
        yield result, server
    finally:
        server.close()


def find(result: ReviewResult, check: str, page: str, title: str = "") -> list[Finding]:
    return [
        f for f in result.findings if f.check == check and f.url.endswith(page) and title in f.title
    ]


def one(result: ReviewResult, check: str, page: str, title: str = "") -> Finding:
    found = find(result, check, page, title)
    assert len(found) == 1, (check, page, title, [f.title for f in result.findings])
    return found[0]


def test_crawls_every_page_once(demo: tuple[ReviewResult, RecordingServer]) -> None:
    result, _ = demo
    paths = sorted(p.url.rsplit("/", 1)[-1] for p in result.pages)
    assert paths == ["", "about.html", "pricing.html"]  # "/" and index.html are one page
    for page in result.pages:
        assert set(page.shots) == ALL_VIEWS
        assert page.status == 200
    assert result.failed


def test_finds_network_and_script_bugs(demo: tuple[ReviewResult, RecordingServer]) -> None:
    result, _ = demo
    assert set(one(result, "broken-image", "/", "/img/hero.png").views) == ALL_VIEWS
    assert one(result, "broken-link", "/", "/blog/").severity is Severity.MAJOR
    assert "classList" in one(result, "page-error", "/").title
    one(result, "failed-request", "/", "/api/stats.json")
    # The hero 404 is reported once, as the broken image, not twice.
    assert not find(result, "failed-request", "/", "hero.png")
    # The blocked tracker pixel is the sandbox's doing, not a broken image.
    assert not find(result, "broken-image", "/", "tracker")


def test_finds_layout_bugs_on_the_right_viewports(
    demo: tuple[ReviewResult, RecordingServer],
) -> None:
    result, _ = demo
    overflow = one(result, "overflow", "pricing.html")
    assert set(overflow.views) == {"mobile-light", "mobile-dark"}
    assert any("section" in s for s in overflow.selectors)
    overlap = one(result, "overlap", "pricing.html")
    assert "NEW PLANS" in overlap.detail and "Simple pricing" in overlap.detail
    clipped = one(result, "clipped-text", "pricing.html")
    assert "Start your free trial" in clipped.detail
    taps = find(result, "tap-target", "pricing.html")
    assert len(taps) == 2
    assert all(not any(v.startswith("desktop") for v in t.views) for t in taps)
    assert all("18x18" in t.detail for t in taps)


def test_finds_accessibility_and_dark_mode_bugs(demo: tuple[ReviewResult, RecordingServer]) -> None:
    result, _ = demo
    assert one(result, "a11y", "/", "image-alt").severity is Severity.CRITICAL
    contrast = one(result, "a11y", "about.html", "color-contrast")
    assert set(contrast.views) == {"mobile-dark", "tablet-dark", "desktop-dark"}
    assert contrast.selectors == ["blockquote"]


def test_no_false_positives_on_the_clean_parts(demo: tuple[ReviewResult, RecordingServer]) -> None:
    result, _ = demo
    about = [
        f for f in result.findings if f.url.endswith("about.html") and f.severity != Severity.INFO
    ]
    assert [f.title for f in about] == [one(result, "a11y", "about.html", "color-contrast").title]
    assert not find(result, "overflow", "/") and not find(result, "overflow", "about.html")
    assert not find(result, "tap-target", "/")  # nav links are padded, the input is 44px tall
    assert not find(result, "overlap", "/")


def test_sandbox_never_sends_anything_but_get(demo: tuple[ReviewResult, RecordingServer]) -> None:
    result, server = demo
    methods = {m for m, _ in server.requests}
    assert methods == {"GET"}, server.requests
    assert not any(path.startswith(("/subscribe", "/collect")) for _, path in server.requests)
    blocked = {(b.method, b.url.rsplit("/", 1)[-1]) for b in result.blocked}
    assert ("POST", "collect") in blocked  # sendBeacon
    assert ("GET", "pixel.gif") in blocked  # off-site tracker
    infos = [f.title for f in result.findings if f.check == "blocked-request"]
    assert "Blocked POST /collect" in infos
    assert "Blocked GET tracker.example.com/pixel.gif" in infos


def test_reports_are_written(demo: tuple[ReviewResult, RecordingServer]) -> None:
    result, _ = demo
    out = result.config.out_dir
    data = json.loads((out / "report.json").read_text())
    assert data["failed"] is True and data["counts"]["critical"] >= 1
    assert len(data["pages"]) == 3
    assert all((out / f["screenshot"]).is_file() for f in data["findings"] if f["screenshot"])
    assert all(f["screenshot"] for f in data["findings"] if f["check"] == "overflow")
    suites = ET.parse(out / "junit.xml").getroot()
    assert int(suites.get("failures", "0")) >= 8
    html = (out / "report.html").read_text()
    assert "<script" not in html and "Page scrolls horizontally" in html
    trace = [json.loads(line) for line in (out / "trace.jsonl").read_text().splitlines()]
    assert trace[-1]["tool"] == "finish" and trace[0]["tool"] == "goto"
    assert {t["phase"] for t in trace} == {"sweep"}
