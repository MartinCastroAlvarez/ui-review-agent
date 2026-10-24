from __future__ import annotations

import pytest

from ui_review.findings import Finding
from ui_review.findings import Findings
from ui_review.findings import Severity


def make(**kw: object) -> Finding:
    data: dict[str, object] = {
        "check": "overflow",
        "severity": Severity.MAJOR,
        "title": "Page scrolls horizontally",
        "url": "http://x/",
        "views": ["mobile-light"],
    }
    data.update(kw)
    return Finding(**data)


def test_severity_parse_and_rank() -> None:
    assert Severity.parse(" Major ") is Severity.MAJOR
    assert Severity.CRITICAL.at_least(Severity.MAJOR)
    assert not Severity.MINOR.at_least(Severity.MAJOR)
    assert Severity.INFO.rank == 0
    with pytest.raises(ValueError, match="severity must be one of"):
        Severity.parse("fatal")


def test_unknown_check_rejected() -> None:
    with pytest.raises(ValueError, match="unknown check"):
        make(check="nope")


def test_long_text_is_clipped() -> None:
    f = make(title="x" * 1000, detail="y" * 10_000, selectors=[f"s{i}" for i in range(30)])
    assert len(f.title) == 300 and f.title.endswith("…")
    assert len(f.detail) == 4000
    assert len(f.selectors) == 10


def test_merge_by_fingerprint_collects_views_and_escalates() -> None:
    fs = Findings()
    a = fs.add(make())
    fs.add(make(views=["mobile-dark"], severity=Severity.CRITICAL, screenshot="s.png"))
    fs.add(make(views=["mobile-dark"]))
    assert len(fs) == 1
    assert a.views == ["mobile-light", "mobile-dark"]
    assert a.severity is Severity.CRITICAL
    assert a.screenshot == "s.png"


def test_key_separates_findings() -> None:
    fs = Findings()
    fs.add(make(check="visual-regression", key="mobile-light"))
    fs.add(make(check="visual-regression", key="mobile-dark"))
    assert len(fs) == 2


def test_sorting_counts_and_filters() -> None:
    fs = Findings()
    fs.add(make(check="clipped-text", severity=Severity.MINOR, title="b"))
    fs.add(make(check="a11y", severity=Severity.CRITICAL, title="a"))
    fs.add(make(check="blocked-request", severity=Severity.INFO, title="c"))
    assert [f.severity for f in fs] == [Severity.CRITICAL, Severity.MINOR, Severity.INFO]
    assert fs.counts() == {"critical": 1, "major": 0, "minor": 1, "info": 1}
    assert [f.title for f in fs.at_least(Severity.MINOR)] == ["a", "b"]
    assert [f.title for f in fs.by_check("a11y")] == ["a"]


def test_discard_and_to_dict() -> None:
    fs = Findings()
    fs.add(make(check="failed-request", detail="http://x/a.png -> HTTP 404", title="a"))
    fs.add(make(check="failed-request", detail="http://x/b.json -> HTTP 404", title="b"))
    assert fs.discard("failed-request", "http://x/", "http://x/a.png") == 1
    (left,) = fs.sorted()
    data = left.to_dict()
    assert data["severity"] == "major" and data["id"] == left.fingerprint
    assert "key" not in data
