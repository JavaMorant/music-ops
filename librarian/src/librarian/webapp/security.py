"""Origin/Host guard for the mutating endpoints.

The server binds 127.0.0.1 only, but a malicious web page the user happens to
visit could still POST to ``http://127.0.0.1:<port>`` (localhost CSRF), and a
DNS-rebinding page could make the browser send a request whose Host is the
attacker's domain pointed at 127.0.0.1. Both carry an ``Origin``/``Referer`` (or
a non-local ``Host``) that isn't ours, so we reject those on state-changing
routes. Same-origin requests from our own page pass; tooling with no Origin and a
local Host passes.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import HTTPException, Request

_ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


def _host_of(value: str) -> str:
    """The bare host of a Host header or an Origin/Referer URL, no port."""
    if "://" in value:
        value = urlsplit(value).hostname or ""
    else:
        value = value.rsplit(":", 1)[0] if value.count(":") == 1 else value
    return value.strip("[]").lower()


def guard_origin(request: Request) -> None:
    """FastAPI dependency: 403 a cross-origin / non-local request."""
    host = _host_of(request.headers.get("host", ""))
    if host and host not in _ALLOWED_HOSTS:
        raise HTTPException(status_code=403, detail="non-local Host header rejected")
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value and _host_of(value) not in _ALLOWED_HOSTS:
            raise HTTPException(status_code=403, detail="cross-origin request rejected")
