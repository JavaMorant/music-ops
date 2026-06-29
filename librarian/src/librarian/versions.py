"""Track-version detection — shared by dedup v2 and quarantine recovery.

Two questions about a title:

  * Is this a **distinct version** that must be KEPT separately (intro edit,
    extended, radio, remix, instrumental, acapella, live, VIP, flip, …)? These
    are different DJ tools even when the underlying song matches.
  * Is this merely an **alternate source** of the SAME recording (an "official
    music video" / "lyrics video" / "official audio" rip)? Those collapse —
    keep the best-quality audio copy.

The recording-level dedup rule (per the design spec) is: collapse exact copies
and alternate-source rips of the same recording, but never collapse two distinct
versions. `base_title()` + `version_label()` give callers the pieces to do that.
"""

from __future__ import annotations

import re

# Tokens that mark a DISTINCT version (keep it separately).
_VERSION_TOKENS = [
    "intro", "outro", "extended", "extended mix", "radio edit", "radio mix",
    "remix", "rmx", "bootleg", "mashup", "mash up", "flip", "vip", "edit",
    "rework", "refix", "dub", "club mix", "club edit", "instrumental", "inst",
    "acapella", "acappella", "accapella", "live", "session", "sped up", "spedup",
    "slowed", "nightcore", "transition", "short edit", "quick hit", "loop",
    "starter", "intro edit", "intro clean", "dirty", "clean",
]
# Tokens that mark the SAME recording from a different SOURCE (collapse these).
_SOURCE_TOKENS = [
    "official music video", "official video", "music video", "lyric video",
    "lyrics video", "lyrics", "lyric", "official audio", "audio", "visualizer",
    "visualiser", "hd", "hq", "4k", "full song", "full version", "official",
]

_VERSION_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in _VERSION_TOKENS) + r")\b", re.I)
_SOURCE_RE = re.compile(r"\b(" + "|".join(re.escape(t) for t in _SOURCE_TOKENS) + r")\b", re.I)
_FEAT_RE = re.compile(r"\b(feat|ft|featuring|with)\b.*", re.I)
_BRACKET_RE = re.compile(r"[\(\[\{].*?[\)\]\}]")


def version_label(title: str) -> str | None:
    """The distinct-version token in ``title`` (e.g. 'intro', 'remix'), or None.

    'clean'/'dirty' alone don't make a version distinct enough to keep apart from
    its plain form unless paired with another token, so they're reported but a
    caller may choose to ignore them. Source-only markers return None.
    """
    m = _VERSION_RE.search(title or "")
    return m.group(1).lower() if m else None


def is_distinct_version(title: str) -> bool:
    """True if the title carries a keep-it-separately version token."""
    return version_label(title) is not None


def is_alt_source(title: str) -> bool:
    """True if the title is only an alternate SOURCE of the same recording
    (music video / lyrics / audio rip) and carries no distinct-version token."""
    t = title or ""
    return bool(_SOURCE_RE.search(t)) and not is_distinct_version(t)


def base_title(title: str) -> str:
    """Normalised song identity: strip featurings, bracketed asides, source and
    version tokens, and punctuation. Two titles share a ``base_title`` when they
    are the same underlying song regardless of version or source."""
    t = (title or "").lower()
    t = _FEAT_RE.sub("", t)
    t = _BRACKET_RE.sub(" ", t)
    t = _VERSION_RE.sub(" ", t)
    t = _SOURCE_RE.sub(" ", t)
    t = re.sub(r"[^a-z0-9]+", "", t)
    return t
