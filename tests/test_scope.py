from __future__ import annotations

import pytest

from ui_review.scope import Scope
from ui_review.scope import normalize
from ui_review.scope import slug


@pytest.mark.parametrize(
    ("url", "base", "expected"),
    [
        ("HTTP://Example.COM:80/a#frag", None, "http://example.com/a"),
        ("https://example.com:443", None, "https://example.com/"),
        ("https://example.com:8443/x?q=1", None, "https://example.com:8443/x?q=1"),
        ("../b", "http://h/a/c", "http://h/b"),
        ("/docs/index.html", "http://h/", "http://h/docs/"),
        ("mailto:a@b.c", None, None),
        ("javascript:alert(1)", None, None),
        ("http://user:pw@h/", None, None),
        ("http://[::1]:8000/x", None, "http://[::1]:8000/x"),
        ("http://h:99999/", None, None),
        ("", None, None),
    ],
)
def test_normalize(url: str, base: str | None, expected: str | None) -> None:
    assert normalize(url, base) == expected


def test_same_origin_only_by_default() -> None:
    scope = Scope("http://127.0.0.1:8000/")
    assert scope.allows("http://127.0.0.1:8000/a.css")
    assert not scope.allows("http://127.0.0.1:8001/")
    assert not scope.allows("https://127.0.0.1:8000/")
    assert not scope.allows("https://cdn.example.com/x.js")
    assert scope.allows("data:image/png;base64,AAAA")
    assert scope.allows("about:blank")
    assert not scope.allows("file:///etc/passwd")
    assert not scope.allows("ftp://127.0.0.1:8000/")


def test_allowlist_exact_and_wildcard() -> None:
    scope = Scope("https://site.test/", ("cdn.example.com", "*.fonts.test"))
    assert scope.allows("https://cdn.example.com/a.js")
    assert not scope.allows("https://evil-cdn.example.com/a.js")
    assert scope.allows("https://a.fonts.test/x.woff2")
    assert not scope.allows("https://fonts.test/x.woff2")
    assert not scope.allows("https://fonts.test.evil.com/x")


def test_crawlable_skips_files_and_foreign_hosts() -> None:
    scope = Scope("http://h/")
    assert scope.crawlable("http://h/about")
    assert not scope.crawlable("http://h/report.pdf")
    assert not scope.crawlable("http://h/logo.PNG")
    assert not scope.crawlable("http://other/")
    assert scope.is_same_origin("http://h:80/x")
    assert not scope.is_same_origin("mailto:x")


def test_scope_rejects_non_http_start() -> None:
    with pytest.raises(ValueError):
        Scope("file:///tmp/x")


def test_slug() -> None:
    start = "http://h/"
    assert slug("http://h/", start) == "index"
    assert slug("http://h/docs/a b.html", start) == "docs-a-b.html"
    assert slug("http://other.test/x", start) == "other.test-x"
    with_query = slug("http://h/search?q=1", start)
    assert with_query.startswith("search-") and len(with_query) == len("search-") + 8
    assert len(slug("http://h/" + "a" * 200, start)) <= 80
