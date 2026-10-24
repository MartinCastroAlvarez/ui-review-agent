from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import SITE
from tests.test_report import result
from ui_review import cli
from ui_review.config import Config
from ui_review.findings import Severity


def test_args_map_onto_config(tmp_path: Path) -> None:
    args = cli.build_parser().parse_args(
        [
            "https://example.com",
            "-o",
            str(tmp_path),
            "--viewports",
            "mobile, desktop",
            "--schemes",
            "dark",
            "--max-pages",
            "4",
            "--max-depth",
            "1",
            "--allow-host",
            "cdn.example.com",
            "--baseline",
            str(tmp_path / "b"),
            "--update-baseline",
            "--tolerance",
            "8",
            "--max-diff",
            "0.01",
            "--no-axe",
            "--no-links",
            "--vision",
            "off",
            "--vision-model",
            "llava",
            "--explore",
            "5",
            "--agent-model",
            "llama3.2",
            "--fail-on",
            "critical",
        ]
    )
    cfg = cli.config_from_args(args)
    assert cfg.viewports == ("mobile", "desktop") and cfg.schemes == ("dark",)
    assert (cfg.max_pages, cfg.max_depth, cfg.pixel_tolerance, cfg.max_diff_ratio) == (
        4,
        1,
        8,
        0.01,
    )
    assert cfg.allow_hosts == ("cdn.example.com",) and cfg.update_baseline
    assert not cfg.axe and not cfg.check_links and cfg.vision == "off"
    assert (cfg.vision_model, cfg.explore_steps, cfg.agent_model) == ("llava", 5, "llama3.2")
    assert cfg.fail_on is Severity.CRITICAL and cfg.baseline_dir == tmp_path / "b"


def test_errors_exit_2(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert cli.main([str(tmp_path / "missing")]) == cli.EXIT_ERROR
    assert "no such directory" in capsys.readouterr().err
    assert cli.main([str(SITE), "--viewports", "watch"]) == cli.EXIT_ERROR


def test_exit_codes_follow_the_gate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def fake_review(config: Config, **_: object):
        return result(tmp_path, config.fail_on)

    monkeypatch.setattr(cli, "review", fake_review)
    assert cli.main(["http://h/", "-o", str(tmp_path / "out")]) == cli.EXIT_FINDINGS
    out = capsys.readouterr().out
    assert "[major   ] overflow" in out and "FAIL:" in out and "note:" in out
    assert "Blocked POST" not in out  # info stays in the report
    assert cli.main(["http://h/", "-q", "--fail-on", "critical"]) == cli.EXIT_OK
    assert capsys.readouterr().out.startswith("PASS:")


@pytest.mark.browser
def test_real_run_on_the_demo_site(
    browser_ok: bool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(
        [
            str(SITE / "pricing.html"),
            "-o",
            str(tmp_path),
            "--viewports",
            "mobile",
            "--schemes",
            "light",
            "--max-pages",
            "1",
            "-q",
        ]
    )
    assert code == cli.EXIT_FINDINGS
    assert (tmp_path / "report.html").is_file() and (tmp_path / "junit.xml").is_file()
    assert "FAIL: 1 pages" in capsys.readouterr().out
