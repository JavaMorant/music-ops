"""Heuristics: infer stage / BPM / key / genre from paths and filenames.

Everything here is a *guess* over messy, human-named folders. The functions are
deliberately conservative — they return ``None``/``"unknown"`` rather than risk
a confident-but-wrong value (e.g. reading the "2" in "2 manny" as a BPM).
"""

from __future__ import annotations

import re
from typing import Optional

# --- stage inference -------------------------------------------------------

# Map a normalised path component to a stage key. Checked against every part of
# a project's path (most-specific taxonomy folder wins). Keep keys lowercased.
_STAGE_MARKERS: list[tuple[str, str]] = [
    ("complete tracks", "complete"),
    ("track list", "track-list"),
    ("remixes in progress", "remix"),
    ("need arranged", "need-arranged"),  # matches "Need Arranged : Deep Progress"
    ("return to", "return-to"),
    ("project bones", "bones"),
    ("mels", "mels"),
]

# Folders that are structural containers, not projects. A loose .flp/.bounce
# sitting directly in one of these is its own standalone item rather than being
# grouped as a folder-project.
STRUCTURAL_NAMES = {
    "beats", "tracks", "projects",
    "complete tracks", "need arranged : deep progress", "need arranged",
    "remixes in progress", "return to", "mels", "project bones", "track list",
    # Complete Tracks sub-buckets:
    "etc", "freestyle", "remix", "song", "songs", "remixes",
    # Return to sub-buckets:
    "potential", "if really bored",
}

# Areas that are sample packs / presets / templates — never real song projects.
# Pruned from the scan walk entirely.
EXCLUDE_NAMES = {
    "backup", "samples", "audio", "drum kits", "diva presets", "loops",
    "other", "presets", "sounds", "plugins", "flps", "exports", ".git",
    "image-line", "ample sound",
}


def infer_stage(rel_parts: list[str]) -> str:
    """Infer a stage key from a project's path components (relative to the scan
    root). The deepest matching taxonomy marker wins, so a project under
    ``Tracks/Return to/Potential`` reads as return-to even though both are
    nested. Returns the default stage when nothing matches."""
    found = "uncategorized"
    for part in rel_parts:
        norm = part.strip().lower()
        for marker, key in _STAGE_MARKERS:
            if marker in norm:
                found = key
    return found


# --- remix vs. original beat ----------------------------------------------

# Whole-word markers that flag a project as a remix / flip / edit of someone
# else's track rather than an original beat. Matched against the name AND the
# path, so a finished flip that graduated out of "Remixes In Progress" into
# Track List / Complete Tracks is still counted as a remix. Whole-word only, so
# "credit"≠edit, "flippant"≠flip.
_REMIX_MARKERS = re.compile(
    r"\b(remix(?:es)?|rmx|flip|bootleg|edit|refix|rework|mashup|vip)\b", re.I
)


def is_remix(name: str, path: str = "", stage: str = "") -> bool:
    """True if a project is a remix/flip/edit rather than an original beat.

    Detected across every stage — via a manually-set ``remix`` stage, the
    folder taxonomy, or the filename — so the dashboard can keep remixes and
    original beats in separate sections regardless of how finished they are.
    """
    if stage == "remix":
        return True
    return bool(_REMIX_MARKERS.search(f"{name}\n{path}"))


# --- BPM / key parsing -----------------------------------------------------

_TOKEN_SPLIT = re.compile(r"[\s_\-.()\[\]]+")
_KEY_FULL = re.compile(r"^([A-Ga-g])([#b]?)(maj|major|min|minor|m)$")
_KEY_ROOT = re.compile(r"^([A-Ga-g])([#b]?)$")
_QUALITY = re.compile(r"^(maj|major|min|minor)$", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(text) if t]


def parse_bpm(text: str) -> Optional[int]:
    """Pull a plausible BPM (60–200) from a name. Only whole standalone tokens
    count, so "2 manny" / "808" / "20202026" never read as a tempo. A token
    explicitly suffixed with 'bpm' is preferred when present."""
    plain: Optional[int] = None
    for tok in _tokens(text):
        low = tok.lower()
        suffixed = low.endswith("bpm")
        digits = low[:-3] if suffixed else low
        if not digits.isdigit():
            continue
        val = int(digits)
        if not (60 <= val <= 200):
            continue
        if suffixed:
            return val
        if plain is None:
            plain = val
    return plain


def _normalise_key(root: str, accidental: str, quality: str) -> str:
    minor = quality.lower() in {"m", "min", "minor"}
    return f"{root.upper()}{accidental}{'m' if minor else ''}"


def parse_key(text: str) -> Optional[str]:
    """Pull a musical key from a name: "Dmin", "C#m", "F_Maj", "F Maj".
    Normalised to DJ shorthand — minor keeps an "m" (Dm, C#m), major drops it
    (F, A). A bare single letter is ignored (too likely an initial)."""
    toks = _tokens(text)
    for i, tok in enumerate(toks):
        m = _KEY_FULL.match(tok)
        if m:
            return _normalise_key(*m.groups())
        # split form: "F" "Maj"  /  "C#" "min"
        r = _KEY_ROOT.match(tok)
        if r and i + 1 < len(toks) and _QUALITY.match(toks[i + 1]):
            return _normalise_key(r.group(1), r.group(2), toks[i + 1])
    return None


# --- genre guess -----------------------------------------------------------

# Ordered: first keyword found in the lowercased path+name wins. Specific
# subgenres come before broad ones.
_GENRE_MARKERS: list[tuple[str, str]] = [
    ("amapiano", "amapiano"),
    ("piano", "amapiano"),
    ("gqom", "gqom"),
    ("afrohouse", "afro house"),
    ("afro", "afro"),
    ("baile", "baile funk"),
    ("jersey", "jersey club"),
    ("drill", "drill"),
    ("garage", "uk garage"),
    ("ukg", "uk garage"),
    ("jungle", "dnb"),
    ("dnb", "dnb"),
    ("drum n bass", "dnb"),
    ("house", "house"),
    ("edm", "edm"),
    ("trap", "trap"),
    ("freestyle", "rap"),
    ("rap", "rap"),
]


_GENRE_PATTERNS = [
    (re.compile(rf"\b{re.escape(marker)}\b"), genre) for marker, genre in _GENRE_MARKERS
]


def guess_genre(text: str) -> str:
    """Match genre markers as whole words, so "scrap"≠rap, "warehouse"≠house,
    "drilling"≠drill. ("piano" still maps to amapiano — that's intended.)"""
    low = text.lower()
    for pattern, genre in _GENRE_PATTERNS:
        if pattern.search(low):
            return genre
    return "unknown"
