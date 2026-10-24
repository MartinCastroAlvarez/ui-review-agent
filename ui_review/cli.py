"""``ui-review <url-or-dir>``: review a site, write reports, exit non-zero on findings."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from ui_review import __version__
from ui_review.config import SCHEMES
from ui_review.config import VIEWPORTS
from ui_review.config import Config
from ui_review.config import ConfigError
from ui_review.findings import Severity
from ui_review.report import write_reports
from ui_review.review import ReviewError
from ui_review.review import review

EXIT_OK, EXIT_FINDINGS, EXIT_ERROR = 0, 1, 2


def _csv(value: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in value.split(",") if v.strip())


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ui-review",
        description="Review a web UI like a QA engineer: crawl, screenshot, check, critique.",
    )
    p.add_argument("target", help="start URL (http/https) or a local static build directory")
    p.add_argument(
        "-o",
        "--out",
        type=Path,
        default=Path("ui-review-report"),
        help="report directory (default: ui-review-report)",
    )
    p.add_argument(
        "--viewports",
        type=_csv,
        default=None,
        help=f"comma list of {', '.join(VIEWPORTS)} (default: all)",
    )
    p.add_argument(
        "--schemes",
        type=_csv,
        default=None,
        help=f"comma list of {', '.join(SCHEMES)} (default: both)",
    )
    p.add_argument("--max-pages", type=int, default=None, help="crawl budget (default 10)")
    p.add_argument("--max-depth", type=int, default=None, help="link depth (default 3)")
    p.add_argument(
        "--allow-host",
        action="append",
        default=[],
        metavar="HOST",
        help="extra host the browser may load (exact or *.example.com); repeatable",
    )
    p.add_argument("--baseline", type=Path, default=None, help="baseline screenshot directory")
    p.add_argument(
        "--update-baseline",
        action="store_true",
        help="write the current screenshots as the new baseline",
    )
    p.add_argument(
        "--tolerance", type=int, default=None, help="per-channel pixel tolerance 0-255 (default 16)"
    )
    p.add_argument(
        "--max-diff",
        type=float,
        default=None,
        help="changed-pixel ratio that counts as a regression (default 0.001)",
    )
    p.add_argument("--no-axe", action="store_true", help="skip the axe-core audit")
    p.add_argument("--no-links", action="store_true", help="skip the broken-link check")
    p.add_argument(
        "--vision",
        choices=["auto", "on", "off"],
        default=None,
        help="vision pass via ollama (default auto: on if the model is pulled)",
    )
    p.add_argument("--vision-model", default=None, help="ollama vision model (qwen2.5vl:7b)")
    p.add_argument(
        "--explore",
        type=int,
        default=None,
        metavar="STEPS",
        help="LLM-driven exploration steps after the sweep (default 0)",
    )
    p.add_argument("--agent-model", default=None, help="ollama tool-calling model (qwen3:8b)")
    p.add_argument(
        "--fail-on",
        default="major",
        choices=[s.value for s in Severity],
        help="lowest severity that fails the run (default major)",
    )
    p.add_argument("-q", "--quiet", action="store_true", help="only print the summary line")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def config_from_args(args: argparse.Namespace) -> Config:
    cfg = Config.from_env()
    changes: dict[str, Any] = {"target": args.target, "out_dir": args.out}
    if args.viewports is not None:
        changes["viewports"] = args.viewports
    if args.schemes is not None:
        changes["schemes"] = args.schemes
    for attr, name in (
        ("max_pages", "max_pages"),
        ("max_depth", "max_depth"),
        ("tolerance", "pixel_tolerance"),
        ("max_diff", "max_diff_ratio"),
        ("vision", "vision"),
        ("vision_model", "vision_model"),
        ("explore", "explore_steps"),
        ("agent_model", "agent_model"),
    ):
        value = getattr(args, attr)
        if value is not None:
            changes[name] = value
    if args.allow_host:
        changes["allow_hosts"] = tuple(args.allow_host)
    if args.baseline is not None:
        changes["baseline_dir"] = args.baseline
    changes["update_baseline"] = args.update_baseline
    changes["axe"] = not args.no_axe
    changes["check_links"] = not args.no_links
    changes["fail_on"] = Severity.parse(args.fail_on)
    return replace(cfg, **changes).validated()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    say = (lambda _m: None) if args.quiet else (lambda m: print(f"ui-review: {m}", file=sys.stderr))
    try:
        config = config_from_args(args)
        result = review(config, log=say)
    except (ConfigError, ReviewError, ValueError) as exc:
        print(f"ui-review: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    paths = write_reports(result)
    if not args.quiet:
        for f in result.findings:
            if f.severity != Severity.INFO:
                print(f"  [{f.severity.value:8}] {f.check:17} {f.title}  ({', '.join(f.views)})")
        for note in result.notes:
            print(f"  note: {note}")
    counts = result.findings.counts()
    summary = ", ".join(f"{n} {s}" for s, n in counts.items())
    verdict = "FAIL" if result.failed else "PASS"
    print(f"{verdict}: {len(result.pages)} pages, {summary}. Report: {paths['html']}")
    return EXIT_FINDINGS if result.failed else EXIT_OK
