"""End-to-end phases with a real browser: baseline, regression, vision and exploration."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import SITE
from tests.conftest import base_config
from tests.conftest import fake_ollama
from ui_review.findings import Severity
from ui_review.report import write_reports
from ui_review.review import ReviewError
from ui_review.review import review

pytestmark = pytest.mark.browser


def small(tmp_path: Path, site: Path, **kw: Any):
    settings: dict[str, Any] = {
        "target": str(site),
        "out_dir": tmp_path / "out",
        "viewports": ("desktop",),
        "schemes": ("light",),
        "max_pages": 1,
        "axe": False,
    }
    return base_config(**(settings | kw))


def test_visual_regression_baseline_then_diff(browser_ok: bool, tmp_path: Path) -> None:
    site = tmp_path / "site"
    shutil.copytree(SITE, site)
    baseline = tmp_path / "baseline"
    first = review(small(tmp_path, site, baseline_dir=baseline))
    assert (baseline / "index__desktop-light.png").is_file()
    assert not first.findings.by_check("visual-regression")

    same = review(small(tmp_path, site, baseline_dir=baseline))
    assert not same.findings.by_check("visual-regression")

    css = site / "style.css"
    css.write_text(css.read_text() + "\nh1 { font-size: 56px; color: #b91c1c; }\n")
    changed = review(small(tmp_path, site, baseline_dir=baseline))
    (reg,) = changed.findings.by_check("visual-regression")
    assert reg.severity in (Severity.MAJOR, Severity.MINOR)
    assert reg.diff and (changed.config.out_dir / reg.diff).is_file()
    assert reg.evidence["changed_pixels"] > 0

    accepted = review(small(tmp_path, site, baseline_dir=baseline, update_baseline=True))
    assert not accepted.findings.by_check("visual-regression")
    assert not review(small(tmp_path, site, baseline_dir=baseline)).findings.by_check(
        "visual-regression"
    )


def test_vision_pass_with_fake_model(browser_ok: bool, tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    answer = {
        "findings": [{"severity": "critical", "title": "Hero image missing", "detail": "Grey box"}]
    }

    def model(path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path == "/api/tags":
            return {"models": [{"name": "qwen2.5vl:7b"}]}
        return {"message": {"content": json.dumps(answer)}}

    result = review(small(tmp_path, SITE, vision="on"), ollama_transport=fake_ollama(model, calls))
    (finding,) = result.findings.by_check("vision")
    assert finding.severity is Severity.MINOR  # advisory: never fails a build alone
    assert finding.source == "vision:qwen2.5vl:7b" and finding.evidence["model_severity"] == "major"
    assert result.vision_model == "qwen2.5vl:7b"
    chat = [b for p, b in calls if p == "/api/chat"]
    assert len(chat) == 1 and chat[0]["messages"][1]["images"]


def test_vision_auto_skips_cleanly_and_on_fails(browser_ok: bool, tmp_path: Path) -> None:
    no_models = fake_ollama(lambda p, b: {"models": []})
    result = review(
        small(tmp_path, SITE, vision="auto", explore_steps=3), ollama_transport=no_models
    )
    assert result.vision_model is None and result.agent_model is None
    assert any("Vision pass skipped" in n for n in result.notes)
    assert any("Exploration skipped" in n for n in result.notes)
    with pytest.raises(ReviewError, match="vision pass required"):
        review(small(tmp_path, SITE, vision="on"), ollama_transport=no_models)


def test_explore_phase_with_fake_agent_model(browser_ok: bool, tmp_path: Path) -> None:
    replies = [
        {
            "content": "Look at pricing on mobile.",
            "tool_calls": [
                {
                    "function": {
                        "name": "goto",
                        "arguments": {"url": "pricing.html", "viewport": "mobile"},
                    }
                }
            ],
        },
        {"content": "", "tool_calls": [{"function": {"name": "run_checks", "arguments": {}}}]},
        {
            "content": "",
            "tool_calls": [
                {"function": {"name": "goto", "arguments": {"url": "https://evil.example/"}}}
            ],
        },
        {"content": "Done."},
    ]

    def model(path: str, body: dict[str, Any]) -> dict[str, Any]:
        if path == "/api/tags":
            return {"models": [{"name": "qwen3:8b"}]}
        return {"message": replies.pop(0)}

    # The sweep sees pricing on desktop only; the model finds the mobile overflow.
    cfg = small(tmp_path, SITE, explore_steps=10, max_pages=2)
    result = review(cfg, ollama_transport=fake_ollama(model))
    explore = [t for t in result.trace if t.phase == "explore"]
    assert [t.tool for t in explore] == ["goto", "run_checks", "goto", "finish"]
    assert not explore[2].ok and "outside the review scope" in explore[2].summary
    (overflow,) = result.findings.by_check("overflow")
    assert overflow.url.endswith("pricing.html") and overflow.views == ["mobile-light"]
    assert result.agent_model == "qwen3:8b"
    paths = write_reports(result)
    assert '"phase": "explore"' in paths["trace"].read_text()


def test_missing_browser_is_a_clear_error(tmp_path: Path) -> None:
    cfg = small(tmp_path, SITE, chromium_path=str(tmp_path / "no-chrome"))
    with pytest.raises(ReviewError, match="could not start Chromium"):
        review(cfg)
