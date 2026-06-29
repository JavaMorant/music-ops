"""Stage 1 — identify the real track behind each file.

Acoustic-fingerprint every file (chromaprint `fpcalc`) and look the fingerprint
up via AcoustID, which returns the real recording's artist + title. This is the
foundation of the organise engine: dedup and classification both get far more
accurate when they see real metadata instead of a junk filename
("Headlines (Drake Cover) Strings Version" → Drake — Headlines).

Degrades gracefully:
  * no AcoustID key            → identity from tags, else from the filename
  * fpcalc/network unavailable → identity from tags, else from the filename
  * fingerprint not in AcoustID → identity from tags, else from the filename

Every lookup is cached by fingerprint, so re-runs are free and a recording
identified once (on any stick) is never looked up again. Add the key later and
re-run: only the still-unidentified files hit the network.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path

from .metadata import read_meta

ACOUSTID_URL = "https://api.acoustid.org/v2/lookup"
_KEY_FILE = Path.home() / "DJ" / ".acoustid-key"
_MIN_INTERVAL = 0.34  # AcoustID free tier ~3 req/s


@dataclass(frozen=True)
class Identity:
    artist: str
    title: str
    source: str            # "acoustid" | "tags" | "filename"
    confidence: float      # 0..1
    mbid: str | None = None


def get_api_key() -> str | None:
    """AcoustID key from ACOUSTID_API_KEY env or ~/DJ/.acoustid-key (first line)."""
    key = os.environ.get("ACOUSTID_API_KEY", "").strip()
    if key:
        return key
    if _KEY_FILE.exists():
        line = _KEY_FILE.read_text(encoding="utf-8").strip().splitlines()
        return line[0].strip() if line else None
    return None


def fingerprint(path: Path) -> tuple[str, int] | None:
    """(fingerprint, duration_seconds) via fpcalc, or None if it can't decode."""
    try:
        out = subprocess.run(
            ["fpcalc", "-json", "-length", "120", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        d = json.loads(out.stdout or "{}")
        if "fingerprint" in d and d.get("duration"):
            return d["fingerprint"], int(float(d["duration"]))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def _from_meta(path: Path) -> Identity:
    """Fallback identity from tags, else a parsed 'Artist - Title' filename."""
    try:
        m = read_meta(path)
        if m.artist or m.title:
            return Identity(m.artist or "", m.title or path.stem, "tags", 0.4)
    except Exception:
        pass
    stem = path.stem
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return Identity(a.strip(), t.strip(), "filename", 0.2)
    return Identity("", stem, "filename", 0.1)


def acoustid_lookup(fp: str, duration: int, key: str) -> Identity | None:
    """Query AcoustID; return the top recording's identity, or None."""
    q = urllib.parse.urlencode({
        "client": key, "duration": duration, "fingerprint": fp,
        "meta": "recordings", "format": "json",
    })
    try:
        with urllib.request.urlopen(f"{ACOUSTID_URL}?{q}", timeout=20) as r:
            data = json.loads(r.read().decode())
    except (OSError, ValueError):
        return None
    if data.get("status") != "ok":
        return None
    best_score, best = 0.0, None
    for res in data.get("results", []):
        score = float(res.get("score", 0))
        for rec in res.get("recordings", []):
            title = rec.get("title")
            artists = ", ".join(a.get("name", "") for a in rec.get("artists", []))
            if title and score >= best_score:
                best_score, best = score, Identity(
                    artists.strip(" ,"), title, "acoustid", round(score, 3), rec.get("id"))
    return best


class IdentityCache:
    """Fingerprint-keyed identity cache persisted as JSON."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except ValueError:
                self._data = {}

    @staticmethod
    def key(fp: str) -> str:
        return hashlib.sha1(fp.encode()).hexdigest()

    def get(self, fp: str) -> Identity | None:
        d = self._data.get(self.key(fp))
        return Identity(**d) if d else None

    def put(self, fp: str, ident: Identity) -> None:
        self._data[self.key(fp)] = asdict(ident)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=1))


def identify(
    paths,
    *,
    key: str | None = None,
    cache: IdentityCache | None = None,
    lookup=acoustid_lookup,
    throttle: float = _MIN_INTERVAL,
) -> dict[Path, Identity]:
    """Identity for each path. Uses AcoustID when ``key`` is set (cached by
    fingerprint), otherwise falls back to tags/filename. ``lookup`` is injectable
    for tests. Network failures degrade per-file, never raise."""
    out: dict[Path, Identity] = {}
    last = 0.0
    for p in map(Path, paths):
        fp_dur = fingerprint(p) if key else None
        if fp_dur and cache is not None:
            cached = cache.get(fp_dur[0])
            if cached is not None:
                out[p] = cached
                continue
        ident = None
        if fp_dur and key:
            wait = throttle - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            ident = lookup(fp_dur[0], fp_dur[1], key)
            last = time.monotonic()
            if ident and cache is not None:
                cache.put(fp_dur[0], ident)
        out[p] = ident or _from_meta(p)
    if cache is not None:
        cache.save()
    return out
