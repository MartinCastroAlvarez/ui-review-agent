from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from tests.conftest import base_config
from ui_review.browser import Blocked
from ui_review.findings import Finding
from ui_review.findings import Findings
from ui_review.findings import Severity
from ui_review.report import junit
from ui_review.report import render_html
from ui_review.report import write_reports
from ui_review.review import ReviewResult
from ui_review.tools import PageRecord

EVIL = '<script>alert(1)</script><img src=x onerror=alert(2)>"\x01'


def result(tmp_path: Path, fail_on: Severity = Severity.MAJOR) -> ReviewResult:
    findings = Findings()
    findings.add(
        Finding(
            "overflow",
            Severity.MAJOR,
            "Page scrolls horizontally",
            "http://h/p",
            detail=EVIL,
            views=["mobile-light"],
            selectors=[EVIL],
            screenshot="screenshots/p.png",
            diff="diffs/p.png",
        )
    )
    findings.add(
        Finding(
            "vision",
            Severity.MINOR,
            EVIL,
            "http://h/p",
            views=["desktop-dark"],
            source="vision:llava",
        )
    )
    findings.add(Finding("blocked-request", Severity.INFO, "Blocked POST /x", "http://h/"))
    page = PageRecord(
        "http://h/p", 1, title=EVIL, status=200, shots={"mobile-light": "screenshots/p.png"}
    )
    cfg = base_config(target="http://h/", out_dir=tmp_path / "out", fail_on=fail_on)
    return ReviewResult(
        config=cfg,
        start_url="http://h/",
        findings=findings,
        pages=[PageRecord("http://h/", 0, status=200), page],
        trace=[],
        blocked=[Blocked("POST", "http://h/x", "never")],
        browser="153",
        notes=[EVIL],
        started_at=1_790_000_000.0,
        duration_s=1.5,
    )


def test_html_escapes_everything_and_has_no_script(tmp_path: Path) -> None:
    html = render_html(result(tmp_path))
    assert "<script" not in html and "<img src=x" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "default-src 'none'" in html
    assert 'src="screenshots/p.png"' in html and 'src="diffs/p.png"' in html
    assert "gate <b>FAILED</b>" in html and "source: vision:llava" in html


def test_junit_is_valid_and_gates_on_severity(tmp_path: Path) -> None:
    root = ET.fromstring(junit(result(tmp_path)))
    assert root.tag == "testsuites" and root.get("failures") == "1"
    failures = root.findall(".//failure")
    assert len(failures) == 1 and failures[0].get("type") == "overflow"
    assert "<script>" in (failures[0].text or "")  # escaped in XML, intact once parsed
    assert any("alert(1)" in (o.text or "") for o in root.findall(".//system-out"))
    lenient = ET.fromstring(junit(result(tmp_path, Severity.CRITICAL)))
    assert lenient.get("failures") == "0"


def test_write_reports(tmp_path: Path) -> None:
    res = result(tmp_path)
    paths = write_reports(res)
    data = json.loads(paths["json"].read_text())
    assert data["failed"] is True and data["counts"] == {
        "critical": 0,
        "major": 1,
        "minor": 1,
        "info": 1,
    }
    assert data["blocked"] == [{"method": "POST", "url": "http://h/x", "reason": "never"}]
    assert data["started_at"].startswith("2026-")
    assert paths["trace"].read_text() == ""
    assert not result(tmp_path, Severity.CRITICAL).failed
