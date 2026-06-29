"""The Organise engine — ties the four stages into reviewable plans.

    identify → dedup v2 → classify → output

Pure/dry-run by default: it reads files and computes plans but mutates nothing.
All disk changes go through the existing reversible engine (apply_plan/journal/undo).
On the master library (~/DJ) we hold every apply for review (plans-only).

Genre source:
  * AcoustID key present (run_ai) → classify v2 on real identity (accurate).
  * no key                       → deterministic OLD-folder → NEW-bucket migration
                                    (rough; refined for free by re-running with a key).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import classify_v2, dedup_v2, identity as idmod
from .dedup_v2 import FileInfo
from .metadata import read_meta
from .model import MOVE, QUARANTINE, Action, Plan
from .paths import audio_files, collision_free, norm_key, quarantine_dest, sanitize_component

# Existing ~/DJ/library folders → the user's 24-bucket taxonomy. Used only when
# no AcoustID key is available (a rough migration; the AI pass supersedes it).
FOLDER_MIGRATION = {
    "Afro House": "Afrohouse", "Afrobeats": "Afrobeats", "Amapiano": "Amapiano",
    "Dance - DnB": "DnB", "Dance - EDM": "General House", "Dance - Tech House": "General House",
    "Edits - Afro Amapiano": "Afro edits", "Edits - Brazilian Phonk": "Other edits",
    "Edits - House": "Other edits", "Edits - Jersey Club": "Jersey Club",
    "Edits - Pop Throwback": "Other edits", "Funk & Disco": "Disco House", "House": "General House",
    "K-Pop": "Pop", "Latin & Brazilian": "Latin", "Pop - 2015+": "Pop", "Pop - pre2015": "Pop",
    "R&B - Modern": "RnB", "R&B - Throwback": "RnB", "Rap - Afro Swing": "Afroswing",
    "Rap - Grime & UK": "UK Rap", "Rap - Trap Hard": "Hype mosh pits", "Rap - Trap Melodic": "US Rap",
    "Rap - UK Drill": "UK Rap", "Rap - US Modern": "US Rap", "Rap - US Throwback": "US Rap",
}
# Folders with no clean 1:1 bucket — always need the AI pass to place accurately.
AMBIGUOUS_FOLDERS = {"Other", "Dancehall", "Rap - Other"}


@dataclass
class OrganiseResult:
    root: Path
    total: int
    dedup: dict
    drops: list = field(default_factory=list)          # FileInfo
    keepers: list = field(default_factory=list)         # FileInfo
    genres: dict = field(default_factory=dict)          # key -> genre
    reorg_actions: int = 0
    needs_ai: int = 0
    used_ai: bool = False


def _file_info(path: Path, ident: idmod.Identity) -> FileInfo:
    try:
        m = read_meta(path)
        br = m.bitrate_kbps or 0
        artist = ident.artist or m.artist or ""
        title = ident.title or m.title or path.stem
    except Exception:
        br, artist, title = 0, ident.artist, ident.title or path.stem
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return FileInfo(path=path, artist=artist, title=title,
                    mbid=ident.mbid, bitrate=br, size=size, rating=0)


def _genre_for(fi: FileInfo, genres: dict, used_ai: bool) -> str | None:
    """Resolved bucket for a keeper: AI verdict if available, else folder migration."""
    if used_ai:
        return genres.get(classify_v2.track_key(fi.artist, fi.title, fi.mbid))
    return FOLDER_MIGRATION.get(fi.path.parent.name)


def organise(
    root: Path,
    *,
    key: str | None = None,
    run_ai: bool = False,
    cache_dir: Path | None = None,
    classify_call=classify_v2.call_anthropic,
) -> OrganiseResult:
    root = Path(root).absolute()
    cache_dir = Path(cache_dir or (root / ".librarian" / "organise"))
    paths = audio_files(root)

    # Stage 1 — identify (degrades without key)
    identities = idmod.identify(
        paths, key=key, cache=idmod.IdentityCache(cache_dir / "identity.json"))
    infos = [_file_info(p, identities[p]) for p in paths]

    # Stage 2 — dedup v2 (recording-level, version-preserving)
    groups = dedup_v2.plan_groups(infos)
    keepers = [g.keep for g in groups]
    drops = [d for g in groups for d in g.drops]

    # Stage 3 — classify (AI on real identity if a key is available)
    genres: dict = {}
    used_ai = bool(run_ai and key)
    if used_ai:
        tracks = [{"key": classify_v2.track_key(k.artist, k.title, k.mbid),
                   "artist": k.artist, "title": k.title,
                   "filename": k.path.name, "genre": ""} for k in keepers]
        genres = {kk: r.genre for kk, r in classify_v2.classify(
            tracks, cache=classify_v2.ClassifyCache(cache_dir / "classify.json"),
            call=classify_call).items()}

    reorg = sum(1 for fi in keepers
                if (b := _genre_for(fi, genres, used_ai)) and fi.path.parent.name != b)
    needs_ai = sum(1 for fi in keepers
                   if not used_ai and (fi.path.parent.name in AMBIGUOUS_FOLDERS
                                       or fi.path.parent.name not in FOLDER_MIGRATION))
    return OrganiseResult(root=root, total=len(paths), dedup=dedup_v2.summarise(groups),
                          drops=drops, keepers=keepers, genres=genres,
                          reorg_actions=reorg, needs_ai=needs_ai, used_ai=used_ai)


def build_dedup_plan(root: Path, drops, rekordbox_xml: Path | None = None) -> Plan:
    """Reversible QUARANTINE plan for dedup drops (never deletes)."""
    root = Path(root).absolute()
    reserved: set[str] = set()
    actions = []
    for fi in drops:
        dest = quarantine_dest(root, fi.path.name, reserved)
        reserved.add(norm_key(dest))
        actions.append(Action(QUARANTINE, fi.path, dest, "recording-level duplicate"))
    return Plan(library_root=root, actions=actions, rekordbox_xml=rekordbox_xml,
                location_redirects={a.src: a.dest for a in actions})


def build_reorg_plan(result: OrganiseResult, rekordbox_xml: Path | None = None) -> Plan:
    """Reversible MOVE plan filing each keeper into its (new) genre bucket."""
    root = result.root
    reserved: set[str] = set()
    actions = []
    for fi in result.keepers:
        bucket = _genre_for(fi, result.genres, result.used_ai)
        if not bucket or fi.path.parent.name == bucket:
            continue
        dest = collision_free(root / sanitize_component(bucket) / fi.path.name,
                              reserved, ignore=fi.path)
        reserved.add(norm_key(dest))
        actions.append(Action(MOVE, fi.path, dest, f"file into {bucket}/"))
    return Plan(library_root=root, actions=actions, rekordbox_xml=rekordbox_xml,
                location_redirects={a.src: a.dest for a in actions})
