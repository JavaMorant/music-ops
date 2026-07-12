"""Camelot-wheel key parsing + harmonic compatibility. Pure, no I/O.

Camelot codes are 1-12 with letter A (minor) or B (major). We parse the three
spellings rekordbox TKEY throws at us — Camelot ("8A"), Open Key ("1m"/"1d"),
and musical ("Am", "C", "F#m", "Ebmaj") — and score how well two keys mix.
"""

from __future__ import annotations

import re

Camelot = tuple[int, str]

# Musical spelling (root lowercased, no maj/min word; minor keeps trailing "m") -> Camelot.
_MUSICAL: dict[str, Camelot] = {
    # majors (B)
    "c": (8, "B"), "g": (9, "B"), "d": (10, "B"), "a": (11, "B"), "e": (12, "B"),
    "b": (1, "B"), "f#": (2, "B"), "gb": (2, "B"), "db": (3, "B"), "c#": (3, "B"),
    "ab": (4, "B"), "g#": (4, "B"), "eb": (5, "B"), "d#": (5, "B"), "bb": (6, "B"),
    "a#": (6, "B"), "f": (7, "B"),
    # minors (A)
    "am": (8, "A"), "em": (9, "A"), "bm": (10, "A"), "f#m": (11, "A"), "gbm": (11, "A"),
    "c#m": (12, "A"), "dbm": (12, "A"), "g#m": (1, "A"), "abm": (1, "A"), "d#m": (2, "A"),
    "ebm": (2, "A"), "a#m": (3, "A"), "bbm": (3, "A"), "fm": (4, "A"), "cm": (5, "A"),
    "gm": (6, "A"), "dm": (7, "A"),
}


def parse_key(s: str | None) -> Camelot | None:
    if not s:
        return None
    t = s.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d{1,2})([ab])", t)          # Camelot: 8a / 12B
    if m:
        n = int(m.group(1))
        return (n, m.group(2).upper()) if 1 <= n <= 12 else None
    m = re.fullmatch(r"(\d{1,2})([dm])", t)          # Open Key: 1d (major) / 1m (minor)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= 12:
            return None
        return ((n + 6) % 12 + 1, "B" if m.group(2) == "d" else "A")
    t = t.replace("major", "").replace("maj", "")    # musical: normalise words to "" / "m"
    t = t.replace("minor", "m").replace("min", "m")
    return _MUSICAL.get(t)


def _step(x: int, y: int) -> int:
    """Distance on the 1-12 clock face."""
    d = abs(x - y) % 12
    return min(d, 12 - d)


def harmonic(a: Camelot | None, b: Camelot | None, *, rising: bool = False,
             mode: str = "loose") -> float:
    if mode == "off":
        return 0.5                                   # harmonic scoring disabled entirely
    if a is None or b is None:
        return 0.5                                   # never punish missing data
    if a == b:
        return 1.0
    (na, la), (nb, lb) = a, b
    if la == lb:
        s = _step(na, nb)
        if s == 1:
            return 0.90
        if s == 2 and rising:
            return 0.55                              # energy boost, rising arc only
        return 0.10
    if na == nb:
        return 0.85                                  # relative major/minor
    if mode == "loose" and _step(na, nb) == 1:
        return 0.40                                  # diagonal energy raise
    return 0.10
