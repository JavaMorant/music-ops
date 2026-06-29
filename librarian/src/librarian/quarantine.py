"""Quarantine recovery — find tracks an earlier dedup set aside that we'd now keep.

The big ``_quarantine`` folder holds dedup DROPS, not broken files. Under the
recording-level rule (design spec), a **distinct version** (intro/extended/remix/
acapella/…) is keep-worthy and should never have been collapsed into its plain
sibling. This module scans the quarantine, groups by song, and surfaces:

  * recover  — quarantined files carrying a distinct-version token (likely wrongly
               dropped; candidates to restore)
  * dupe     — alternate-source rips (official video / lyrics / audio) of the same
               recording (correctly set aside)
  * plain    — a plain copy with no version/source marker (ordinary dedup drop)

It never moves anything — it produces a report + a restore list to be reviewed
(plans-only on ~/DJ).
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from pathlib import Path

from . import versions as V
from .metadata import read_meta
from .paths import AUDIO_EXTS


@dataclass(frozen=True)
class QItem:
    path: Path
    artist: str
    title: str
    base: str
    version: str | None   # distinct-version token, if any
    alt_source: bool
    kind: str             # "recover" | "dupe" | "plain"


def _classify(title: str) -> tuple[str | None, bool, str]:
    ver = V.version_label(title)
    if ver:
        return ver, False, "recover"
    if V.is_alt_source(title):
        return None, True, "dupe"
    return None, False, "plain"


def _label(meta_artist: str, meta_title: str, fname: str) -> tuple[str, str]:
    """Best (artist, title): prefer tags, fall back to 'Artist - Title' filename."""
    if meta_artist or meta_title:
        return meta_artist or "", meta_title or Path(fname).stem
    stem = Path(fname).stem
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return a.strip(), t.strip()
    return "", stem


def scan(qdir: Path) -> list[QItem]:
    """Scan a quarantine folder into classified items (reads tags; moves nothing)."""
    qdir = Path(qdir)
    items: list[QItem] = []
    if not qdir.exists():
        return items
    for p in sorted(qdir.iterdir()):
        if not p.is_file() or p.suffix.lower() not in AUDIO_EXTS:
            continue
        try:
            m = read_meta(p)
            artist, title = _label(m.artist or "", m.title or "", p.name)
        except Exception:
            artist, title = _label("", "", p.name)
        ver, alt, kind = _classify(title)
        items.append(QItem(p, artist, title, V.base_title(title), ver, alt, kind))
    return items


def group_by_song(items: list[QItem]) -> dict[str, list[QItem]]:
    groups: dict[str, list[QItem]] = collections.defaultdict(list)
    for it in items:
        groups[it.base].append(it)
    return dict(groups)


def recovery_report(qdir: Path, wanted: list[str] | None = None) -> str:
    """Markdown report. ``wanted`` is a list of song names to highlight (matched
    on base title), e.g. ['Promiscuous', 'Wannabe']."""
    items = scan(qdir)
    groups = group_by_song(items)
    recover = [it for it in items if it.kind == "recover"]
    dupes = [it for it in items if it.kind == "dupe"]
    plain = [it for it in items if it.kind == "plain"]
    wanted_bases = {V.base_title(w) for w in (wanted or [])}

    L = [f"# Quarantine recovery — {qdir}", "",
         f"- audio files in quarantine: **{len(items):,}**",
         f"- **recover** (distinct versions wrongly dropped): **{len(recover):,}**",
         f"- dupe (alt-source rips of same recording): {len(dupes):,}",
         f"- plain (ordinary dedup drops): {len(plain):,}", ""]

    if wanted_bases:
        L += ["## Wanted tracks", ""]
        for w in (wanted or []):
            wb = V.base_title(w)
            hits = groups.get(wb, [])
            L.append(f"### {w} — {len(hits)} copy(ies) in quarantine")
            for it in hits:
                tag = {"recover": "↩ recover", "dupe": "dupe", "plain": "plain"}[it.kind]
                v = f" [{it.version}]" if it.version else ""
                L.append(f"- {tag}{v} — `{it.path.name}`")
            L.append("")

    if recover:
        L += ["## Distinct versions to restore (candidates)", ""]
        for it in sorted(recover, key=lambda x: (x.base, x.version or "")):
            who = f"{it.artist} - {it.title}".strip(" -")
            L.append(f"- [{it.version}] {who} — `{it.path.name}`")
        L.append("")
    return "\n".join(L) + "\n"


def restore_list(qdir: Path) -> list[Path]:
    """Paths of the distinct-version files that are candidates to restore."""
    return [it.path for it in scan(qdir) if it.kind == "recover"]
