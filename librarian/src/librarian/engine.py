"""The plan/apply/undo engine.

This is the one place in librarian that mutates the filesystem, and every line
of it is built around the safety invariants:

  * **Journal before execute.** The run is written to disk (status ``applying``,
    all actions ``pending``) before the first move, and re-flushed after every
    move. A crash leaves a journal that exactly reflects what happened.
  * **Never delete, never clobber.** Every action is a move; a move that would
    overwrite an existing file is refused. No file is ever removed.
  * **Fully reversible.** ``undo_run`` reverses the completed moves in reverse
    order and restores the rekordbox XML from its backup.
"""

from __future__ import annotations

import os
import shutil
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

from . import rekordbox, tags
from .journal import (
    APPLIED,
    APPLYING,
    DONE,
    PENDING,
    REVERTED,
    UNDONE,
    Journal,
    JournalAction,
    JournalTagEdit,
    load_journal,
    new_run_id,
    write_journal,
)
from .model import WRITABLE_TAGS, Plan


class EngineError(RuntimeError):
    """A safety check failed; the filesystem was left untouched."""


def _norm(path: Path) -> str:
    """Case-folded absolute path, for collision/containment comparisons.

    macOS volumes are case-insensitive but ``os.path.normcase`` is a no-op on
    POSIX, so we casefold explicitly, and NFC-normalise so a decomposed (NFD)
    filename from the filesystem compares equal to a composed (NFC) one from a
    tag or plan. This is deliberately conservative: on a case-sensitive volume
    it treats names differing only in case as colliding, which for a tool that
    writes to the library is the safe direction.
    """
    return unicodedata.normalize("NFC", os.path.abspath(path)).casefold()


def _same_existing_file(a: Path, b: Path) -> bool:
    """True if both exist and are the same file (e.g. a case-only rename on a
    case-insensitive volume), where moving a onto b is safe, not a clobber."""
    try:
        return a.exists() and b.exists() and a.samefile(b)
    except OSError:
        return False


def _nearest_existing(path: Path) -> Path:
    """The closest ancestor of ``path`` (or ``path`` itself) that exists."""
    p = path
    while not p.exists():
        if p.parent == p:  # reached the filesystem root
            return p
        p = p.parent
    return p


def _same_device(a: Path, b: Path) -> bool:
    """True if ``a`` and the nearest existing ancestor of ``b`` share a volume.

    A move across volumes can't be an atomic rename — stdlib would fall back to
    copy-then-delete, which is neither crash-safe nor a true move — so the
    engine refuses it rather than risk the source. (Quarantine and renames are
    in-library, so the same-device path is the normal one.)
    """
    try:
        return a.stat().st_dev == _nearest_existing(b).stat().st_dev
    except OSError:
        return False


def _is_real_dir(path: Path) -> bool:
    """A directory that is not a symlink — a dest the engine must never move into."""
    return path.is_dir() and not path.is_symlink()


def _safe_move(src: Path, dest: Path) -> None:
    """Move ``src`` -> ``dest`` without ever destroying data.

    Refuses if ``dest`` already exists as a *different* file or as a directory,
    or if the move would cross volumes. Creates parent directories. Handles a
    case-only rename on case-insensitive filesystems via a temporary
    intermediate so the bytes are never lost.
    """
    if not src.exists():
        raise EngineError(f"source missing, refusing to move: {src}")
    if _is_real_dir(dest):
        raise EngineError(f"destination is a directory, refusing to move into it: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not _same_device(src, dest):
        raise EngineError(
            f"refusing cross-volume move (not an atomic rename): {src} -> {dest}"
        )

    if dest.exists():
        if _same_existing_file(src, dest):
            # Same file, different spelling — rename via a temp name in the
            # destination directory so we never have to delete anything.
            tmp = dest.with_name(dest.name + ".librarian-rename-tmp")
            if tmp.exists():
                raise EngineError(f"rename temp already exists: {tmp}")
            os.rename(src, tmp)
            os.rename(tmp, dest)
            return
        raise EngineError(f"destination already exists, refusing to overwrite: {dest}")

    # Same-device, dest free: a pure rename. os.rename never copies or deletes.
    os.rename(src, dest)


def _within(root: Path, path: Path) -> bool:
    """True if ``path`` is ``root`` or sits beneath it (case-insensitive)."""
    root_n, path_n = _norm(root), _norm(path)
    return path_n == root_n or path_n.startswith(root_n + os.sep)


def preflight(plan: Plan) -> None:
    """Validate the whole plan before any change. Raises EngineError on the
    first problem, guaranteeing an all-or-nothing-safe apply.

    The apply input can be an arbitrary reviewed JSON file, so this is also the
    boundary that keeps a (mis)edited plan from escaping the library or
    clobbering anything.
    """
    if plan.rekordbox_xml is not None:
        if not plan.rekordbox_xml.is_file():
            raise EngineError(f"rekordbox XML not found: {plan.rekordbox_xml}")
        # Parse it now, before any file moves: a malformed XML (or one with no
        # <COLLECTION> we'd add tracks to) must fail the whole plan up front, not
        # crash mid-apply after every file has already been relocated.
        try:
            xml_root = ET.parse(plan.rekordbox_xml).getroot()
        except ET.ParseError as exc:
            raise EngineError(f"rekordbox XML is not parseable: {plan.rekordbox_xml}: {exc}")
        if plan.rekordbox_additions and xml_root.find("COLLECTION") is None:
            raise EngineError(f"rekordbox XML has no <COLLECTION> to add tracks to: {plan.rekordbox_xml}")

    root = plan.library_root
    # Keys are case-normalised so a case-only collision can't slip through on a
    # case-insensitive volume.
    seen_dests: dict[str, Path] = {}
    for action in plan.actions:
        src, dest = action.src, action.dest
        if not src.exists():
            raise EngineError(f"planned source does not exist: {src}")
        # Containment: never move into or out of anywhere but the library root.
        if not _within(root, src):
            raise EngineError(f"source escapes the library root {root}: {src}")
        if not _within(root, dest):
            raise EngineError(f"destination escapes the library root {root}: {dest}")
        # Never move onto an existing directory.
        if _is_real_dir(dest):
            raise EngineError(f"destination is a directory: {dest}")
        # Two actions must never target the same destination (case-insensitively).
        key = _norm(dest)
        prior = seen_dests.get(key)
        if prior is not None:
            raise EngineError(f"two actions target the same destination {dest}: {prior} and {src}")
        seen_dests[key] = src
        # The destination must be free, unless it's the very file we're moving
        # (a case-only rename of the same file).
        if dest.exists() and not _same_existing_file(src, dest):
            raise EngineError(f"destination already exists, refusing to overwrite: {dest}")
        # Cross-volume moves can't be atomic renames — refuse up front.
        if not _same_device(src, dest):
            raise EngineError(f"cross-volume move refused: {src} -> {dest}")

    # Tag repairs: validate every one before a single byte is written, so the
    # tag phase is all-or-nothing-safe too. (An apply input can be arbitrary
    # reviewed JSON, so this is the boundary that keeps a hand-edited plan from
    # writing a forbidden tag or touching a file outside the library.)
    for edit in plan.tag_edits or []:
        if not edit.path.exists():
            raise EngineError(f"tag-edit source does not exist: {edit.path}")
        if not _within(root, edit.path):
            raise EngineError(f"tag-edit path escapes the library root {root}: {edit.path}")
        bad = [k for k in edit.fields if k not in WRITABLE_TAGS]
        if bad:
            raise EngineError(f"refusing to write non-writable tag(s) {bad} on {edit.path}")
        if any(not isinstance(v, str) for v in edit.fields.values()):
            raise EngineError(f"tag values must be strings: {edit.path}")
        if not tags.is_taggable(edit.path):
            raise EngineError(f"file cannot carry tags, refusing to retag: {edit.path}")


def _backup_sources(plan: Plan, run_dir: Path, full: bool) -> dict:
    """Copy at-risk files into ``run_dir/backup`` before any move.

    By default this is every file the plan touches; ``full`` snapshots the whole
    library. Either way it's a safety net independent of the undo journal —
    the brief's "back up before the first apply" — and it lives outside the
    library so it never pollutes the tree. Returns journal bookkeeping.
    """
    root = plan.library_root.absolute()
    backup_dir = run_dir / "backup"
    if full:
        sources = [p for p in root.rglob("*") if p.is_file()]
    else:
        # Both moved files and tag-repaired files are at risk; back up each once.
        sources = [a.src for a in plan.actions] + [t.path for t in (plan.tag_edits or [])]
        seen: set[str] = set()
        sources = [s for s in sources if not (_norm(s) in seen or seen.add(_norm(s)))]
    copied = 0
    for src in sources:
        src = src.absolute()
        try:
            rel = src.relative_to(root)
        except ValueError:
            rel = Path(src.name)  # outside root (shouldn't happen post-preflight)
        dest = backup_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied += 1
    return {"dir": str(backup_dir), "mode": "full" if full else "sources", "files": copied}


def apply_plan(plan: Plan, runs_dir: Path, *, backup: bool = True, full_backup: bool = False) -> Journal:
    """Execute a reviewed plan, journaling every step. Returns the Journal.

    ``backup`` (default on) copies every file the plan touches into the run's
    backup dir before anything moves; ``full_backup`` snapshots the whole
    library instead. Pass ``backup=False`` only when a separate backup or
    copy-mode already protects the originals.
    """
    preflight(plan)

    run_id = new_run_id()
    run_dir = runs_dir / run_id
    journal = Journal(
        run_id=run_id,
        library_root=plan.library_root,
        actions=[JournalAction(action=a, status=PENDING) for a in plan.actions],
        tag_edits=[
            JournalTagEdit(path=e.path, old={}, new=dict(e.fields), status=PENDING)
            for e in (plan.tag_edits or [])
        ],
        status=APPLYING,
        created=run_id[:15],  # YYYYmmdd-HHMMSS prefix of the run id
        run_dir=run_dir,
    )

    # Journal before anything: the run is recorded on disk before we copy or
    # move a single byte, so even a crash during backup leaves an auditable run
    # that `librarian runs` will list.
    run_dir.mkdir(parents=True, exist_ok=True)
    write_journal(journal)

    # Safety net: back up at-risk files before the first move, then re-journal
    # so the backup is part of the audit trail.
    if backup:
        journal.backup = _backup_sources(plan, run_dir, full_backup)
        write_journal(journal)

    # Back up the rekordbox XML *before* anything moves, so even a crash
    # mid-apply leaves undo able to restore the collection.
    if plan.rekordbox_xml is not None:
        rb_backup = run_dir / "rekordbox.orig.xml"
        shutil.copy2(plan.rekordbox_xml, rb_backup)
        journal.rekordbox = {
            "original": str(plan.rekordbox_xml),
            "backup": str(rb_backup),
            "rewritten": False,
        }
        write_journal(journal)

    # Tag repairs run first, on the files' current (pre-move) paths. For each we
    # read the OLD values and journal them *before* writing the new ones, so a
    # crash still leaves undo able to restore exactly what was there.
    for tedit in journal.tag_edits:
        tedit.old = tags.read_tags(tedit.path, tedit.new.keys())
        write_journal(journal)
        tags.write_tags(tedit.path, tedit.new)
        tedit.status = DONE
        write_journal(journal)

    for entry in journal.actions:
        _safe_move(entry.action.src, entry.action.dest)
        entry.status = DONE
        write_journal(journal)

    # Now that every file is at its new home, point rekordbox at the new paths.
    # Arm the journal's restore flag and flush it *before* touching the XML —
    # journal-before-execute again — so a crash during the rewrite still leaves
    # undo able (and obliged) to restore the collection. If the crash lands
    # before the rewrite runs, the backup is byte-identical to the original, so
    # the restore is a harmless no-op.
    if plan.rekordbox_xml is not None:
        # Start from every literal move, then overlay the plan's explicit
        # redirects (which send a quarantined dup's cues to the kept copy).
        # Overlaying — not replacing — means a partial redirect map can never
        # leave a moved file with a dead Location.
        path_map = {a.action.src: a.action.dest for a in journal.actions}
        path_map.update(plan.location_redirects or {})
        journal.rekordbox["rewritten"] = True
        write_journal(journal)
        rekordbox.rewrite_locations(plan.rekordbox_xml, path_map, plan.rekordbox_xml)
        # Then ADD any brand-new tracks (inbox imports) + their playlist. This
        # runs after the rewrite and operates on disjoint paths (the new tracks
        # aren't in the collection yet). It's covered by the same XML backup, so
        # undo restores byte-for-byte — the `rewritten` flag is already armed.
        if plan.rekordbox_additions:
            rekordbox.add_tracks_and_playlist(
                plan.rekordbox_xml, plan.rekordbox_additions, plan.rekordbox_playlist
            )

    journal.status = APPLIED
    write_journal(journal)
    return journal


def undo_run(run_id: str, runs_dir: Path, *, library_root: Path | None = None) -> Journal:
    """Reverse a run completely: every completed move back to where it was, and
    the rekordbox XML restored byte-for-byte from its backup.

    ``run_id`` is treated as an opaque token: it must be a single path component
    naming a direct child of ``runs_dir``. This stops a crafted id like
    ``../evil`` from loading an arbitrary journal and moving files anywhere.

    ``library_root``, when given (the web app passes its trusted, launch-time
    root), is the containment boundary AND must match the journal's recorded
    root — so even a fully forged journal can't relocate a file outside the root
    the operator actually chose. When omitted (the CLI), containment falls back
    to the journal's own root; the run_id guard is the real boundary there.
    """
    # run_id must be one path component and resolve to a direct child of runs_dir.
    if not run_id or "/" in run_id or "\\" in run_id or run_id in (".", ".."):
        raise EngineError(f"invalid run id: {run_id!r}")
    run_dir = runs_dir / run_id
    if run_dir.resolve().parent != runs_dir.resolve():
        raise EngineError(f"invalid run id (escapes runs dir): {run_id!r}")
    if not (run_dir / "journal.json").exists():
        raise EngineError(f"no run found with id {run_id} under {runs_dir}")

    journal = load_journal(run_dir)
    if journal.status == UNDONE:
        raise EngineError(f"run {run_id} has already been undone")

    # Containment boundary: a trusted root if supplied, else the journal's own.
    if library_root is not None and _norm(journal.library_root) != _norm(library_root):
        raise EngineError(
            f"journal library root {journal.library_root} does not match {library_root}"
        )
    root = library_root or journal.library_root
    for entry in journal.actions:
        if entry.status == DONE:
            if not _within(root, entry.action.src) or not _within(root, entry.action.dest):
                raise EngineError(
                    f"refusing undo: action escapes library root {root}: {entry.action.src}"
                )
    for tedit in journal.tag_edits:
        if tedit.status == DONE and not _within(root, tedit.path):
            raise EngineError(
                f"refusing undo: tag-edit path escapes library root {root}: {tedit.path}"
            )

    # Reverse the done moves in the opposite order they were applied.
    for entry in reversed(journal.actions):
        if entry.status == DONE:
            _safe_move(entry.action.dest, entry.action.src)
            entry.status = REVERTED
            write_journal(journal)

    # Files are back at their original paths now, so restore their tags to the
    # pre-run values the journal captured (a recorded None deletes a tag that was
    # absent before the repair). Tags were applied before moves, so they're undone
    # after — back to the exact starting state.
    for tedit in reversed(journal.tag_edits):
        if tedit.status == DONE:
            tags.write_tags(tedit.path, tedit.old)
            tedit.status = REVERTED
            write_journal(journal)

    # Restore the rekordbox collection from the backup — but only if this run
    # actually rewrote it, so we never clobber a live XML the user has since
    # edited after a run that crashed before the rewrite. Restore atomically.
    if journal.rekordbox is not None and journal.rekordbox.get("rewritten"):
        backup = Path(journal.rekordbox["backup"])
        original = Path(journal.rekordbox["original"])
        if backup.is_file():
            tmp = original.with_name(original.name + ".librarian-restore-tmp")
            shutil.copy2(backup, tmp)
            os.replace(tmp, original)

    journal.status = UNDONE
    write_journal(journal)
    return journal
