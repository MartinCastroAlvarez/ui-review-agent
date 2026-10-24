"""Which URLs the agent may load: the start origin, plus hosts the operator allowlists."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}
# Links to these are files, not pages: they are link-checked but never crawled.
_NOT_PAGES = re.compile(
    r"\.(?:pdf|zip|gz|tgz|png|jpe?g|gif|webp|avif|svg|ico|mp[34]|webm|mov|woff2?|ttf|css|js|json|xml|txt)$",
    re.IGNORECASE,
)
# URLs a page can use without any network request.
_LOCAL_SCHEMES = ("data", "blob", "about")


def normalize(url: str, base: str | None = None) -> str | None:
    """Absolute ``http(s)`` URL without fragment or default port, or ``None``."""
    try:
        joined = urljoin(base, url.strip()) if base else url.strip()
        parts = urlsplit(joined)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    path = parts.path or "/"
    if path.endswith("/index.html"):
        path = path[: -len("index.html")]
    netloc = host if port in (None, _DEFAULT_PORTS[parts.scheme]) else f"{host}:{port}"
    return urlunsplit((parts.scheme, netloc, path, parts.query, ""))


def origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    scheme = parts.scheme
    return scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(scheme, 0)


class Scope:
    """Same-origin by default. ``allow_hosts`` entries are exact hosts or ``*.example.com``."""

    def __init__(self, start_url: str, allow_hosts: tuple[str, ...] = ()) -> None:
        start = normalize(start_url)
        if start is None:
            raise ValueError(f"not an http(s) URL: {start_url!r}")
        self.start_url = start
        self.origin = origin(start)
        self.allow_hosts = tuple(h.lower() for h in allow_hosts)

    def allows(self, url: str) -> bool:
        """May the browser send a request to ``url``?"""
        scheme = url.split(":", 1)[0].lower()
        if scheme in _LOCAL_SCHEMES:
            return True
        norm = normalize(url)
        if norm is None:
            return False
        if origin(norm) == self.origin:
            return True
        host = origin(norm)[1]
        return any(_host_matches(host, pattern) for pattern in self.allow_hosts)

    def crawlable(self, url: str) -> bool:
        """May the agent navigate to ``url`` as a page to review?"""
        norm = normalize(url)
        if norm is None or not self.allows(norm):
            return False
        return not _NOT_PAGES.search(urlsplit(norm).path)

    def is_same_origin(self, url: str) -> bool:
        norm = normalize(url)
        return norm is not None and origin(norm) == self.origin


def _host_matches(host: str, pattern: str) -> bool:
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return host.endswith(suffix) and len(host) > len(suffix)
    return host == pattern


def slug(url: str, start_url: str) -> str:
    """A file-system safe, stable name for a page."""
    parts = urlsplit(url)
    path = parts.path.strip("/") or "index"
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", path).strip("-.") or "index"
    if origin(url) != origin(start_url):
        name = re.sub(r"[^A-Za-z0-9.-]+", "-", parts.hostname or "host") + "-" + name
    if parts.query or len(name) > 80:
        digest = hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()[:8]
        name = f"{name[:70]}-{digest}"
    return name
