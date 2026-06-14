"""Mode B — the forever pipeline, as a reviewable plan.

`build_inbox_plan` drains a watched Inbox/ folder: each dropped file is read,
deduped against the EXISTING library (and against the rest of the drop), and —
if genuinely new — filed into the canonical `Genre/Artist - Title` structure and
queued to be added to the rekordbox collection + a "New This Week" playlist.
Duplicates go to quarantine (never deleted). Everything routes through the same
reversible plan/apply/undo engine; this module only *decides*, it never moves.

This is a one-shot batch (run on demand), not a live daemon: a watcher can't be
dry-run/reviewed, which the safety model requires. The Inbox must live inside the
library root and on the same volume (so the engine's containment + atomic-rename
guarantees hold).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .metadata import content_hash, read_meta
from .model import MOVE, QUARANTINE, Action, Plan, RekordboxAddition
from .naming import pick_keeper, target_stem
from .paths import audio_files, collision_free, norm_key, quarantine_dest, sanitize_component
from .planner import _DUP_MARKER

_KIND = {
    ".mp3": "MP3 File",
    ".wav": "WAV File",
    ".aiff": "AIFF File",
    ".aif": "AIFF File",
    ".flac": "FLAC File",
    ".m4a": "M4A File",
    ".aac": "AAC File",
    ".ogg": "OGG File",
}


class InboxError(ValueError):
    """The inbox configuration is invalid (e.g. not inside the library)."""


@dataclass
class InboxReport:
    library_root: Path
    inbox_dir: Path
    total_files: int = 0
    imported: int = 0
    dup_library: list[tuple[Path, Path]] = field(default_factory=list)   # (candidate, keeper)
    dup_batch: list[tuple[Path, Path]] = field(default_factory=list)
    suspected: list[Path] = field(default_factory=list)
    low_bitrate: list[tuple[Path, int]] = field(default_factory=list)
    missing_key: list[Path] = field(default_factory=list)
    missing_tags: list[Path] = field(default_factory=list)

    def render(self) -> str:
        L = ["# Inbox report", "", f"Library: `{self.library_root}`", f"Inbox: `{self.inbox_dir}`", ""]
        L += [
            "## Summary",
            f"- Inbox files scanned: {self.total_files}",
            f"- Imported (new): {self.imported}",
            f"- Skipped — already in library: {len(self.dup_library)}",
            f"- Skipped — duplicate within this drop: {len(self.dup_batch)}",
            f"- Quarantined — suspected duplicate: {len(self.suspected)}",
            f"- Low-bitrate (re-acquire): {len(self.low_bitrate)}",
            f"- Missing musical key: {len(self.missing_key)}",
            f"- Missing artist/title tags: {len(self.missing_tags)}",
            "",
        ]
        if self.dup_library:
            L.append("## Already in library → quarantined (nothing deleted)")
            L += [f"- `{c.name}` — duplicate of `{k.name}`" for c, k in self.dup_library]
            L.append("")
        if self.low_bitrate:
            L.append("## Low bitrate — re-acquire a proper copy (never auto-downloaded)")
            L += [f"- `{p.name}` — {kbps} kbps" for p, kbps in self.low_bitrate]
            L.append("")
        return "\n".join(L)


def _kind(path: Path) -> str | None:
    return _KIND.get(path.suffix.lower())


def _under(parent: Path, child: Path) -> bool:
    return parent == child or parent in child.parents


def build_inbox_plan(
    library_root: Path,
    inbox_dir: Path,
    *,
    organize_by_genre: bool = True,
    rekordbox_xml: Path | None = None,
    playlist_name: str = "New This Week",
) -> tuple[Plan, InboxReport]:
    root = library_root.absolute()
    inbox = inbox_dir.absolute()
    # Containment: the engine refuses moves whose src escapes the library root,
    # so the inbox must live inside it. Check the *resolved* paths too, so a
    # symlinked inbox pointing outside the library is caught here, not after the
    # files have moved. Fail clearly rather than at apply.
    if (
        inbox == root
        or not _under(root, inbox)
        or not _under(root.resolve(), inbox.resolve())
    ):
        raise InboxError(f"inbox {inbox} must be a real folder inside the library root {root}")

    candidates = audio_files(inbox)
    report = InboxReport(library_root=root, inbox_dir=inbox, total_files=len(candidates))

    # Index the existing library (everything under root except the inbox subtree
    # and quarantine). Bucket by size; only hash a candidate's size-peers, so a
    # genuinely-new track with a unique size needs zero library hashing.
    library_files = [p for p in audio_files(root) if not _under(inbox, p)]
    by_size: dict[int, list[Path]] = {}
    for p in library_files:
        try:
            by_size.setdefault(p.stat().st_size, []).append(p)
        except OSError:
            continue
    _lib_hash: dict[Path, str] = {}

    def library_dup(cand: Path, cand_hash: str) -> Path | None:
        try:
            peers = by_size.get(cand.stat().st_size, [])
        except OSError:
            return None
        for p in peers:
            h = _lib_hash.get(p)
            if h is None:
                h = _lib_hash[p] = content_hash(p)
            if h == cand_hash:
                return p
        return None

    metas = {c: read_meta(c) for c in candidates}
    hashes = {c: content_hash(c) for c in candidates}

    # Group the drop by content hash; keep the highest-quality copy of each.
    by_hash: dict[str, list[Path]] = {}
    for c in candidates:
        by_hash.setdefault(hashes[c], []).append(c)

    actions: list[Action] = []
    reserved: set[str] = set()
    redirects: dict[Path, Path] = {}
    additions: list[RekordboxAddition] = []

    def reserve(p: Path) -> None:
        # Same key the collision helpers and the engine use (NFC + casefold), so
        # two new tracks whose names differ only by Unicode form aren't both
        # emitted (the engine would reject the plan as a same-dest collision).
        reserved.add(norm_key(p))

    # Deterministic order: by each group's keeper path.
    groups = []
    for group in by_hash.values():
        groups.append((pick_keeper(group, metas), group))
    groups.sort(key=lambda kg: str(kg[0]))

    for keeper, group in groups:
        # Within-drop duplicates: quarantine every copy but the keeper, and point
        # any cues at the keeper (resolved after the keeper's own fate below).
        batch_dups = [c for c in sorted(group, key=str) if c is not keeper]
        for c in batch_dups:
            dest = quarantine_dest(root, c.name, reserved)
            reserve(dest)
            actions.append(Action(QUARANTINE, c, dest, f"exact duplicate of {keeper.name} (earlier in this drop)"))
            report.dup_batch.append((c, keeper))

        # Is the keeper already in the library? -> quarantine it, point cues at
        # the library copy, and do NOT import it.
        lib = library_dup(keeper, hashes[keeper])
        if lib is not None:
            dest = quarantine_dest(root, keeper.name, reserved)
            reserve(dest)
            actions.append(Action(QUARANTINE, keeper, dest, f"exact duplicate of {lib.name} (already in library)"))
            redirects[keeper] = lib
            for c in batch_dups:  # batch copies' cues also land on the library copy
                redirects[c] = lib
            report.dup_library.append((keeper, lib))
            continue

        # Suspected duplicate by name (copy marker), not byte-identical.
        if _DUP_MARKER.search(keeper.stem):
            dest = quarantine_dest(root, keeper.name, reserved)
            reserve(dest)
            actions.append(Action(QUARANTINE, keeper, dest, "suspected duplicate (copy marker)"))
            report.suspected.append(keeper)
            continue

        # Genuinely new -> file it into the canonical structure + queue the add.
        meta = metas[keeper]
        if meta.low_bitrate:
            report.low_bitrate.append((keeper, meta.bitrate_kbps or 0))
        if not meta.key:
            report.missing_key.append(keeper)
        if not meta.has_artist_title:
            report.missing_tags.append(keeper)

        stem = target_stem(meta)
        folder = root / sanitize_component(meta.genre) if (organize_by_genre and meta.genre) else root
        dest = collision_free(folder / (stem + keeper.suffix.lower()), reserved, ignore=keeper)
        reserve(dest)
        where = dest.parent.name if dest.parent != root else "library root"
        actions.append(Action(MOVE, keeper, dest, f"import from inbox into {where}"))
        for c in batch_dups:  # within-drop copies' cues land on the imported keeper
            redirects[c] = dest
        additions.append(
            RekordboxAddition(
                location=dest,
                name=meta.title or stem,
                artist=meta.artist,
                genre=meta.genre,
                total_time=int(meta.length_s) if meta.length_s else None,
                average_bpm=meta.bpm,
                tonality=meta.key,
                bitrate_kbps=meta.bitrate_kbps,
                kind=_kind(keeper),
            )
        )
        report.imported += 1

    plan = Plan(
        library_root=root,
        actions=actions,
        rekordbox_xml=rekordbox_xml,
        location_redirects=redirects or None,
        rekordbox_additions=additions or None,
        rekordbox_playlist=playlist_name or None,
    )
    return plan, report
