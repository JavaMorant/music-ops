"""Metadata de-duplication — the complement to the acoustic/exact-hash dedupe.

Acoustic fingerprinting catches the *same recording*; it can't merge the same
song downloaded from different YouTube uploads (different waveforms). This pass
groups by a noise-stripped canonical "Artist - Title", so

  Burna Boy - Last Last (Official Music Video)
  Burna Boy - Last Last [Audio]
  Last Last (2)

collapse into one group. It is deliberately conservative:

  - **Stems** (``-bass``/``-drums``/``-vocals`` …) are never grouped.
  - **Version markers** (Remix/Edit/Intro/Clean/Dirty …) keep copies *distinct*
    (a DJ wants both the clean and the dirty).
  - A group whose quality copies are **different lengths** (radio vs full) is
    routed to manual review, never auto-merged.

The chosen merges become a normal :class:`Plan` of ``QUARANTINE`` actions with
``location_redirects`` pointing each dropped dup at the kept copy — so it rides
the existing reversible engine: undo journal, never-delete, rekordbox cue/
playlist safety.
"""
from __future__ import annotations

import os
import re
import statistics
from pathlib import Path

from mutagen import File as MutagenFile

from .model import QUARANTINE, Action, Plan
from .paths import audio_files, norm_key, quarantine_dest

_NOISE = re.compile(
    r"\b(official\s*(music\s*)?(video|audio)|lyric[s]?\s*video|lyric[s]?|"
    r"visuali[sz]er|music\s*video|audio\s*only|audio|video|hd|hq|4k|320\s*kbps|320kbps|"
    r"128\s*kbps|free\s*(download|dl)|out\s*now|official|directed\s*by[^)]*)\b", re.I)
_PAREN = re.compile(r"[\(\[\{][^)\]\}]*[\)\]\}]")
_KEEP = re.compile(
    r"\b(remix|edit|flip|bootleg|vip|refix|rework|mashup|dub|instrumental|acapella|"
    r"acap|extended|sped\s*up|slowed|amapiano|version|mix|intro|outro|clean|dirty|"
    r"short|snip|starter|transition|quick|loop|live)\b", re.I)
_STEM = re.compile(r"-(bass|drums|vocals?|other|piano|guitar|instrumental|melody|keys|synth|fx|music)$", re.I)
_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F300-\U0001F9FF🎵🎼🎶]")
_LOSSLESS_EXT = {".flac", ".wav", ".aiff", ".aif"}


def _tags(fp: Path) -> tuple[str, str]:
    try:
        m = MutagenFile(fp, easy=True) or {}
        return (m.get("artist", [""])[0], m.get("title", [""])[0])
    except Exception:
        return "", ""


def _canon(fp: Path) -> str:
    artist, title = _tags(fp)
    base = _EMOJI.sub(" ", (f"{artist} - {title}"
                            if artist and title and artist.lower() not in title.lower()
                            else title or fp.stem))
    s = _PAREN.sub(lambda m: m.group(0) if _KEEP.search(m.group(0)) else " ", base)
    s = _NOISE.sub(" ", s)
    s = re.sub(r"\(\s*\d+\s*\)\s*$", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _probe(fp: Path) -> dict:
    """Quality via mutagen (fast, no subprocess): kbps, seconds, lossless?"""
    kbps, secs = 0, 0.0
    try:
        m = MutagenFile(fp)
        if m is not None and m.info is not None:
            kbps = int(getattr(m.info, "bitrate", 0) or 0) // 1000
            secs = float(getattr(m.info, "length", 0) or 0)
    except Exception:
        pass
    lossless = fp.suffix.lower() in _LOSSLESS_EXT
    return {"br": kbps, "dur": secs, "lossless": lossless}


def _nclean(fp: Path) -> int:
    """Lower is a cleaner name — used only as a survivor tiebreak."""
    bn = fp.name
    return (len(_NOISE.findall(bn))
            + (1 if re.search(r"\(\s*\d+\s*\)", bn) else 0)
            + (0 if " - " in bn else 1))


_SKIP_PLAYLISTS = {"CUE Analysis Playlist"}  # rekordbox-internal, not user playlists


def rekordbox_playlists_by_path() -> dict[str, list[str]]:
    """``{normalised abspath: [playlist names the track is in]}`` — best effort.
    Empty dict if rekordbox/pyrekordbox is unavailable. Lets the UI show *which*
    playlists each copy belongs to, and prefers a playlist-connected survivor."""
    import collections
    try:
        from pyrekordbox import Rekordbox6Database
        from pyrekordbox.db6 import tables as T
        db = Rekordbox6Database()
        names = {str(pl.ID): pl.Name for pl in db.get_playlist()
                 if getattr(pl, "Attribute", 0) == 0 and pl.Name not in _SKIP_PLAYLISTS}
        by_content: dict[str, set[str]] = collections.defaultdict(set)
        for r in db.session.query(T.DjmdSongPlaylist):
            nm = names.get(str(r.PlaylistID))
            if nm:
                by_content[str(r.ContentID)].add(nm)
        out: dict[str, list[str]] = {}
        for c in db.get_content():
            p = c.FolderPath or ""
            if p:
                out[norm_key(Path(p))] = sorted(by_content.get(str(c.ID), ()))  # engine key
        return out
    except Exception:
        return {}


def _file_row(fp: Path, root: Path, pl_map: dict[str, list[str]]) -> dict:
    pr = _probe(fp)
    qual = " · ".join(x for x in [
        fp.suffix.lstrip(".") if pr["lossless"] else "",
        f"{pr['br']}kbps" if pr["br"] else "",
        f"{int(pr['dur'])}s" if pr["dur"] else "",
    ] if x) or "unreadable"
    playlists = pl_map.get(norm_key(fp), [])
    return {
        "rel": str(fp.relative_to(root)),
        "br": pr["br"], "dur": round(pr["dur"]), "lossless": pr["lossless"],
        "size": fp.stat().st_size if fp.exists() else 0,
        "pl": len(playlists), "playlists": playlists,
        "quality": qual, "nclean": _nclean(fp),
    }


def analyze(library_root: Path, *, pl_map: dict[str, list[str]] | None = None) -> dict:
    """Group the library into auto-merge / manual-review / mixed buckets."""
    root = library_root.absolute()
    pl_map = pl_map or {}
    groups: dict[str, list[Path]] = {}
    for fp in audio_files(root):
        if _STEM.search(fp.stem):
            continue
        k = _canon(fp)
        if k and len(k) > 3:
            groups.setdefault(k, []).append(fp)

    auto, review, mixed = [], [], []
    for key, paths in groups.items():
        if len(paths) < 2:
            continue
        if any(_KEEP.search(p.name) for p in paths):
            mixed.append({"key": key, "kind": "mixed",
                          "files": [_file_row(p, root, pl_map) for p in sorted(paths)]})
            continue
        rows = [_file_row(p, root, pl_map) for p in paths]
        durs = [r["dur"] for r in rows if r["dur"] > 30]
        med = statistics.median(durs or [r["dur"] for r in rows] or [0])
        good = [r for r in rows if r["br"] >= 128 and r["dur"] > 30]
        gd = [r["dur"] for r in good]
        if len(good) >= 2 and gd and max(gd) / max(min(gd), 1) > 1.2:
            review.append({"key": key, "kind": "review",
                           "files": sorted(rows, key=lambda r: -r["dur"])})
            continue

        def score(r):
            # quality first (right-length, lossless, bitrate); among equals prefer
            # the most playlist-connected copy, then the cleanest filename.
            within = 1 if med and 0.8 * med <= r["dur"] <= 1.25 * med else 0
            return (within, r["lossless"], r["br"], r["pl"], -r["nclean"])

        keep = max(rows, key=score)
        for r in rows:
            r["keep"] = (r["rel"] == keep["rel"])
        auto.append({
            "key": key, "kind": "auto", "keep": keep["rel"],
            "files": sorted(rows, key=lambda r: (not r["keep"], -r["br"])),
        })

    auto.sort(key=lambda g: -len(g["files"]))
    review.sort(key=lambda g: -len(g["files"]))
    mixed.sort(key=lambda g: -len(g["files"]))
    drop_n = sum(len(g["files"]) - 1 for g in auto)
    reclaim = sum(r["size"] for g in auto for r in g["files"] if not r["keep"])
    return {
        "auto": auto, "review": review, "mixed": mixed,
        "stats": {
            "auto_groups": len(auto), "review_groups": len(review), "mixed_groups": len(mixed),
            "to_quarantine": drop_n, "reclaim_mb": round(reclaim / 1e6),
            "rekordbox": bool(pl_map),
        },
    }


def group_memberships(analysis: dict) -> list[set[str]]:
    """The rel-sets a client is allowed to merge *within* — only the auto-merge
    and manual-review groups. Version variants (``mixed``) are deliberately
    excluded so they can never be quarantined through the API."""
    return [{f["rel"] for f in g["files"]}
            for g in (analysis.get("auto", []) + analysis.get("review", []))]


def valid_decisions(analysis: dict, decisions: list[dict]) -> list[dict]:
    """Keep only decisions whose keep+drops all belong to one real duplicate
    group — so a crafted request can't quarantine two unrelated tracks."""
    groups = group_memberships(analysis)
    out = []
    for d in decisions:
        members = {d.get("keep"), *d.get("drop", [])}
        if d.get("keep") and any(members <= g for g in groups):
            out.append(d)
    return out


def build_plan(library_root: Path, decisions: list[dict], rekordbox_xml: Path | None = None) -> Plan:
    """Turn confirmed merges into a reversible quarantine Plan.

    ``decisions`` = ``[{"keep": rel, "drop": [rel, ...]}, ...]``. Every dropped
    dup is quarantined (never deleted) and rekordbox is repointed at the kept
    copy, so playlists/cues survive."""
    root = library_root.absolute()
    rootstr = str(root)
    actions: list[Action] = []
    redirects: dict[Path, Path] = {}
    reserved: set[str] = set()

    def inside(p: Path) -> bool:
        # abspath (not resolve) — match the engine/rekordbox convention so a
        # symlinked library root never makes every path look "outside".
        ap = os.path.abspath(p)
        return ap == rootstr or ap.startswith(rootstr + os.sep)

    for d in decisions:
        keep = root / d["keep"]
        # only operate inside the library, on a real keeper
        if not inside(keep) or not keep.exists():
            continue
        keep = Path(os.path.abspath(keep))
        for rel in d.get("drop", []):
            src = root / rel
            if not inside(src) or not src.exists():
                continue
            src = Path(os.path.abspath(src))
            if src == keep:
                continue
            dest = quarantine_dest(root, src.name, reserved)
            reserved.add(norm_key(dest))
            actions.append(Action(QUARANTINE, src, dest,
                                  f"duplicate of {keep.name} (kept best copy)"))
            redirects[src] = keep  # keeper stays in place → cues/playlists follow it
    return Plan(library_root=root, actions=actions,
                rekordbox_xml=rekordbox_xml, location_redirects=redirects)
