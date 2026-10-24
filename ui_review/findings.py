"""Findings: what the review found, with severity, evidence and the views it shows up in."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"

    @property
    def rank(self) -> int:
        return _RANK[self]

    def at_least(self, other: Severity) -> bool:
        return self.rank >= other.rank

    @classmethod
    def parse(cls, value: str) -> Severity:
        try:
            return cls(value.strip().lower())
        except ValueError as exc:
            names = ", ".join(s.value for s in cls)
            raise ValueError(f"severity must be one of {names}: {value!r}") from exc


_RANK = {Severity.INFO: 0, Severity.MINOR: 1, Severity.MAJOR: 2, Severity.CRITICAL: 3}

# Every check the agent can report, in the order the report lists them.
CHECKS: dict[str, str] = {
    "http-status": "Page answered with an HTTP error",
    "load": "Page did not load",
    "page-error": "Uncaught JavaScript exception",
    "console-error": "console.error output",
    "failed-request": "Same-origin request failed",
    "blocked-request": "Request blocked by the review sandbox",
    "broken-image": "Image failed to load",
    "broken-link": "Link target returns an error",
    "overflow": "Horizontal overflow",
    "overlap": "Overlapping text",
    "clipped-text": "Clipped text",
    "tap-target": "Tap target too small",
    "a11y": "Accessibility (axe-core)",
    "visual-regression": "Visual regression against the baseline",
    "vision": "Vision model critique",
}


@dataclass
class Finding:
    check: str
    severity: Severity
    title: str
    url: str
    detail: str = ""
    views: list[str] = field(default_factory=list)
    selectors: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    screenshot: str | None = None
    diff: str | None = None
    source: str = "deterministic"
    key: str = ""

    def __post_init__(self) -> None:
        if self.check not in CHECKS:
            raise ValueError(f"unknown check {self.check!r}")
        self.title = _clip(self.title, 300)
        self.detail = _clip(self.detail, 4000)
        self.selectors = [_clip(s, 300) for s in self.selectors[:10]]

    @property
    def fingerprint(self) -> str:
        raw = "\x1f".join([self.check, self.url, self.title, self.key, *self.selectors[:3]])
        return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        data["id"] = self.fingerprint
        data.pop("key")
        return data


class Findings:
    """Findings merged by fingerprint: the same bug on six views is one finding."""

    def __init__(self) -> None:
        self._items: dict[str, Finding] = {}

    def add(self, finding: Finding) -> Finding:
        existing = self._items.get(finding.fingerprint)
        if existing is None:
            self._items[finding.fingerprint] = finding
            return finding
        for view in finding.views:
            if view not in existing.views:
                existing.views.append(view)
        if finding.severity.rank > existing.severity.rank:
            existing.severity = finding.severity
        existing.screenshot = existing.screenshot or finding.screenshot
        existing.diff = existing.diff or finding.diff
        return existing

    def discard(self, check: str, url: str, detail_prefix: str) -> int:
        """Drop ``check`` findings on ``url`` whose detail starts with ``detail_prefix``."""
        doomed = [
            key
            for key, f in self._items.items()
            if f.check == check and f.url == url and f.detail.startswith(detail_prefix)
        ]
        for key in doomed:
            del self._items[key]
        return len(doomed)

    def __iter__(self) -> Iterator[Finding]:
        return iter(self.sorted())

    def __len__(self) -> int:
        return len(self._items)

    def sorted(self) -> list[Finding]:
        order = list(CHECKS)
        return sorted(
            self._items.values(),
            key=lambda f: (-f.severity.rank, order.index(f.check), f.url, f.title),
        )

    def at_least(self, severity: Severity) -> list[Finding]:
        return [f for f in self.sorted() if f.severity.at_least(severity)]

    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for f in self._items.values():
            out[f.severity.value] += 1
        return out

    def by_check(self, check: str) -> list[Finding]:
        return [f for f in self.sorted() if f.check == check]


def _clip(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"
