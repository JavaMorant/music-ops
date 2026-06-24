"""Deterministic Artist/Title tag cleanup — proposals for the reversible retag
engine (no AI, no guessing).

Goal: a consistently-formatted library *in the tags* (what rekordbox and the CDJ
display) without renaming files. We:

  - strip download noise: ``(Official Video)``, ``(Official Audio)``, ``(Lyrics)``,
    ``(320 kbps)``, emoji, uploader handles (``- musiclover11``, ``_spotdown.org``),
    trailing ``(2)``/``(3)`` copy markers;
  - split ``Artist - Title`` into the proper fields when the artist is baked into
    the title (or the artist field holds a duplicate / a YouTube uploader);
  - **preserve** real parenthetical subtitles (``(Call Me By Your Name)``) and
    version markers (``(Remix)``/``(Intro Clean)``/``(feat. …)``) untouched.

Casing is left alone (stylised titles like ``GOOBA`` / ``M E R C Y`` must survive).
Each changed file becomes a ``TagProposal``; ``retag.build_retag_plan`` turns those
into journaled, undoable ``TagEdit``s.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import tags
from .paths import audio_files
from .retag import TagProposal

_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F300-\U0001F9FF🎵🎼🎶]")
# noise words/phrases — used to decide a bracket group is junk (not a real subtitle)
_NOISE = re.compile(
    r"official\s*music\s*video|official\s*video|official\s*audio|\bofficial\b|"
    r"lyric[s]?\s*video|\blyric[s]?\b|music\s*video|audio\s*only|visuali[sz]er|"
    r"\d{2,3}\s*kbps|free\s*(?:download|dl)|out\s*now|\bhd\b|\bhq\b|\b4k\b|directed\s*by[^)\]]*",
    re.I)
_PARENS = re.compile(r"[\(\[\{][^)\]\}]*[\)\]\}]")
# a bracket whose ENTIRE content is one of these is a standalone noise tag, e.g.
# "[Audio]" / "(HD)" — strip it, while a bare "audio"/"video" inside a real title
# (outside brackets, or part of a longer subtitle) is left alone.
_STANDALONE = {"audio", "video", "hd", "hq", "4k", "official", "lyrics", "lyric",
               "m/v", "mv", "visualizer", "clip", "hq audio", "official audio", "official video"}
_EXT = re.compile(r"\.(mp3|wav|m4a|flac|aiff?|ogg)\s*$", re.I)
# trailing run of bare noise words (no brackets), e.g. "… Song OFFICIAL MUSIC VIDEO HD"
_TRAIL_NOISE = re.compile(
    r"(?:\s*[-–—|]?\s*(?:official|music|video|audio|lyric[s]?|visualizer|hd|hq|4k))+\s*$", re.I)
_HANDLE = re.compile(r"\s*[-_]\s*(?:musiclover\d+|spotdown\.org|topic|prod\.?\s*\w+)\s*$", re.I)
_KBPS = re.compile(r"\s*[\(\[]?\s*\d{2,3}\s*kbps\s*[\)\]]?", re.I)
_COPYMARK = re.compile(r"\s*\(\s*\d+\s*\)\s*$")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _paren_is_noise(grp: str) -> bool:
    return bool(_NOISE.search(grp)) or grp[1:-1].strip().lower() in _STANDALONE


def _clean_text(s: str) -> str:
    s = _EMOJI.sub(" ", s or "")
    s = _EXT.sub("", s)               # title accidentally carried the file extension
    # drop bracket groups that are download noise; keep real subtitles/versions
    s = _PARENS.sub(lambda m: " " if _paren_is_noise(m.group(0)) else m.group(0), s)
    # collapse a redundant "X ( X )" wrap (export artefact) down to "X"
    m = re.match(r"^(.*?)\s*[\(\[]\s*(.+?)\s*[\)\]]\s*$", s)
    if m and _norm(m.group(1)) and _norm(m.group(1)) == _norm(m.group(2)):
        s = m.group(1)
    s = _KBPS.sub(" ", s)
    s = _HANDLE.sub(" ", s)
    s = _COPYMARK.sub(" ", s)
    s = _TRAIL_NOISE.sub("", s)
    s = re.sub(r"\s*[-–—]\s*$", "", s)              # dangling trailing dash
    return re.sub(r"\s{2,}", " ", s).strip(" -–—·_")


def _split(s: str) -> tuple[str, str]:
    parts = s.split(" - ", 1)
    if len(parts) == 2 and parts[0].strip():
        return parts[0].strip(), parts[1].strip()
    return "", s.strip()


def clean_fields(artist: str, title: str, stem: str = "") -> tuple[str, str]:
    """Return cleaned ``(artist, title)`` — deliberately conservative.

    - The TITLE is always scrubbed of download noise.
    - An existing artist is NEVER overwritten (so a "Title - Remixer" file can't
      get its artist flipped). If the title merely repeats that known artist as a
      leading ``Artist - `` prefix, the duplicate prefix is dropped.
    - Only when the file has NO artist do we derive one by splitting ``Artist -
      Title`` (filling a gap, not rewriting a value).
    """
    a, t = (artist or "").strip(), (title or "").strip()
    nt = _clean_text(t)
    if a:
        pre, sep, rest = nt.partition(" - ")
        if sep and rest.strip() and _norm(pre) == _norm(a):
            nt = rest.strip()            # title duplicated the known artist — drop it
        return a, nt                     # keep the existing artist verbatim
    da, dt = _split(_clean_text(t or stem))
    return (da, dt) if da else ("", nt or dt)


def build_proposals(library_root: Path) -> list[TagProposal]:
    """Scan the library and propose cleaned Artist/Title for files that change."""
    root = library_root.absolute()
    props: list[TagProposal] = []
    for fp in audio_files(root):
        if not tags.is_taggable(fp):
            continue
        try:
            cur = tags.read_tags(fp, ("artist", "title"))
        except tags.TagError:
            continue
        a, t = cur.get("artist") or "", cur.get("title") or ""
        na, nt = clean_fields(a, t, fp.stem)
        fields: dict[str, str] = {}
        if nt and nt != t:
            fields["title"] = nt
        if na and na != a:
            fields["artist"] = na
        if fields:
            props.append(TagProposal(path=fp, fields=fields,
                                     reason="clean Artist/Title", confidence="high"))
    return props
