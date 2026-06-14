"""Mode A — the one-time deep clean, as a reviewable plan.

`build_cleanup_plan` reads each track's metadata and proposes *moves only* (no
byte-level tag writes — that's a later, separately-reversible slice):

  1. **Exact-duplicate dedupe** (byte-identical files) → quarantine all but the
     highest-quality copy.
  2. **Copy-marker heuristic** — files ending in `` (1)``, `` (2)`` … that aren't
     exact dupes are still set aside as suspected duplicates.
  3. **Rename** survivors to `Artist - Title` from their tags (falling back to a
     cleaned filename when tags are missing — never guessed).
  4. **Refile** into `Genre/` folders (configurable; only files that actually
     carry a genre tag are moved, so nothing lands in a junk "Unknown" pile).

Everything routes through the same reversible engine, and a `CleanupReport`
captures the flags (low bitrate, missing key, missing tags) for review.
"""

from __future__ import annotations

from pathlib import Path

from .metadata import content_hash, read_meta
from .model import MOVE, QUARANTINE, Action, Plan
from .naming import pick_keeper
from .naming import target_stem as _target_stem
from .paths import audio_files, collision_free, norm_key, quarantine_dest, sanitize_component
from .planner import _DUP_MARKER
from .report import CleanupReport


def _audio_files(root: Path) -> list[Path]:
    return audio_files(root)


def build_cleanup_plan(
    library_root: Path,
    organize_by_genre: bool = True,
    rekordbox_xml: Path | None = None,
) -> tuple[Plan, CleanupReport]:
    root = library_root.absolute()
    files = _audio_files(root)
    metas = {p: read_meta(p) for p in files}

    report = CleanupReport(library_root=root, total_files=len(files))
    actions: list[Action] = []
    reserved: set[str] = set()
    quarantined: set[Path] = set()
    # For each duplicate we quarantine, remember which copy we kept, so rekordbox
    # can be repointed at the keeper instead of following cues into quarantine.
    dup_keeper: dict[Path, Path] = {}

    def reserve(path: Path) -> None:
        reserved.add(norm_key(path))

    # 1) Exact-duplicate dedupe — group by content hash, keep the best copy.
    by_hash: dict[str, list[Path]] = {}
    for p in files:
        by_hash.setdefault(content_hash(p), []).append(p)
    for group in by_hash.values():
        if len(group) < 2:
            continue
        # Highest quality wins; on a tie, prefer a clean name over a copy-marker
        # one (else the marker copy could win and then be quarantined by step 2,
        # dropping the whole group). Deterministic via the sorted `files` order.
        keeper = pick_keeper(group, metas)
        for p in group:
            if p == keeper:
                continue
            dest = quarantine_dest(root, p.name, reserved)
            reserve(dest)
            reason = f"exact duplicate of {keeper.name} (kept higher quality)"
            actions.append(Action(QUARANTINE, p, dest, reason))
            quarantined.add(p)
            dup_keeper[p] = keeper
            report.duplicates.append((p, keeper, reason))

    # 2) Copy-marker heuristic for whatever survived dedupe.
    for p in files:
        if p in quarantined or not _DUP_MARKER.search(p.stem):
            continue
        dest = quarantine_dest(root, p.name, reserved)
        reserve(dest)
        reason = "suspected duplicate (copy marker)"
        actions.append(Action(QUARANTINE, p, dest, reason))
        quarantined.add(p)
        report.duplicates.append((p, None, reason))

    # 3 + 4) Rename + refile survivors; collect report flags along the way.
    for p in files:
        if p in quarantined:
            continue
        meta = metas[p]
        if meta.low_bitrate:
            report.low_bitrate.append((p, meta.bitrate_kbps or 0))
        if not meta.key:
            report.missing_key.append(p)
        if not meta.has_artist_title:
            report.missing_tags.append(p)

        stem = _target_stem(meta)
        folder = root / sanitize_component(meta.genre) if (organize_by_genre and meta.genre) else p.parent
        ideal = folder / (stem + p.suffix.lower())

        if ideal == p:
            report.left_in_place += 1
            continue
        dest = collision_free(ideal, reserved, ignore=p)
        reserve(dest)

        renamed = dest.name != p.name
        refiled = dest.parent != p.parent
        if refiled:
            report.refiled += 1
        if renamed:
            report.renamed += 1
        reasons = []
        if renamed:
            reasons.append("rename to Artist - Title" if meta.has_artist_title else "clean filename")
        if refiled:
            reasons.append(f"file into {dest.parent.name}/")
        actions.append(Action(MOVE, p, dest, "; ".join(reasons) or "normalise"))

    # Where rekordbox should be pointed for each old path. Policy: never leave a
    # dead pointer. Every moved/quarantined file's Location follows it to its new
    # path (so even a quarantined file's cues still resolve — nothing is deleted).
    # Exact-duplicate quarantines do better: they repoint to the keeper that
    # stays in the library, wherever it ends up. (Copy-marker suspected dups have
    # no known keeper, so they follow the file into quarantine.)
    final_of = {a.src: a.dest for a in actions}
    redirects: dict[Path, Path] = dict(final_of)
    for dup, keeper in dup_keeper.items():
        redirects[dup] = final_of.get(keeper, keeper)

    plan = Plan(
        library_root=root,
        actions=actions,
        rekordbox_xml=rekordbox_xml,
        location_redirects=redirects,
    )
    return plan, report
