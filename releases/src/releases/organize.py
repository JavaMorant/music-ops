"""The folder plan/apply/undo engine — the ONLY place releases writes to the
music library.

It mirrors the librarian's proven safety design, adapted for whole FL-project
*folders* (and the occasional loose file):

  * **Dry-run first.** A reviewable Plan (old → new + reason) is produced and
    saved; nothing moves until ``apply`` runs it.
  * **Journal before execute.** The run is written to disk (status ``applying``,
    actions ``pending``) before the first move and re-flushed (atomic + fsync)
    after each one, so a crash leaves an accurate, undoable record.
  * **Never delete, never clobber.** Every action is a same-volume rename; a
    move onto an existing path is refused. No file or folder is ever removed.
  * **Containment.** Source and destination must both stay inside the library
    root, so a (hand-edited) plan can never relocate anything outside it.
  * **Fully reversible.** ``undo_run`` renames everything back, newest first.

A directory rename on one volume is a single atomic syscall — a folder move is
all-or-nothing, so no half-moved project is possible. Pure moves are losslessly
reversible from the journal, so no bulk copy-backup is needed (that would mean
duplicating gigabytes of Audio/Samples for no added safety).
"""

from __future__ import annotations

import json
import os
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from . import tags
from .scan import AUDIO_EXTS

MOVE = "move"      # file a project into a stage folder / relocate it
RENAME = "rename"  # rename a project folder in place
ACTION_KINDS = (MOVE, RENAME)

# Per-action / per-run lifecycle (same vocabulary as the librarian journal).
PENDING, DONE, REVERTED = "pending", "done", "reverted"
APPLYING, APPLIED, UNDOING, UNDONE = "applying", "applied", "undoing", "undone"
JOURNAL_NAME = "journal.json"


class OrganizeError(RuntimeError):
    """A safety check failed; the filesystem was left untouched."""


class ApplyInterrupted(OrganizeError):
    """A move failed mid-run. Carries the partial journal (with the moves that
    DID complete recorded) so the caller can re-link the index and the run stays
    undoable. The completed moves are intact on disk; only the rest didn't run."""

    def __init__(self, message: str, journal: "Journal"):
        super().__init__(message)
        self.journal = journal


@dataclass(frozen=True)
class Action:
    """One folder/file operation: move ``src`` → ``dest`` because of ``reason``.
    ``stage`` (optional) is the destination stage, recorded so the index can be
    re-stamped after a successful move."""

    kind: str
    src: Path
    dest: Path
    reason: str
    stage: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ACTION_KINDS:
            raise ValueError(f"unknown action kind: {self.kind!r}")
        if self.src == self.dest:
            raise ValueError(f"action src and dest are identical: {self.src}")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "src": str(self.src), "dest": str(self.dest),
                "reason": self.reason, "stage": self.stage}

    @classmethod
    def from_dict(cls, d: dict) -> Action:
        return cls(kind=d["kind"], src=Path(d["src"]), dest=Path(d["dest"]),
                   reason=d["reason"], stage=d.get("stage"))


@dataclass(frozen=True)
class TagEdit:
    """A reversible ID3 tag write on a file that is NOT moved by this edit. The
    OLD values are read and journaled at apply time, never stored here, so undo
    restores exactly what was there."""

    path: Path
    fields: dict        # writable tag name -> new value (genre/comment)
    reason: str = ""

    def to_dict(self) -> dict:
        return {"path": str(self.path), "fields": dict(self.fields), "reason": self.reason}

    @classmethod
    def from_dict(cls, d: dict) -> TagEdit:
        return cls(path=Path(d["path"]), fields=dict(d["fields"]), reason=d.get("reason", ""))


@dataclass
class Plan:
    library_root: Path
    actions: list[Action]
    tag_edits: list[TagEdit] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"library_root": str(self.library_root),
                "actions": [a.to_dict() for a in self.actions],
                "tag_edits": [t.to_dict() for t in self.tag_edits]}

    @classmethod
    def from_dict(cls, d: dict) -> Plan:
        return cls(library_root=Path(d["library_root"]),
                   actions=[Action.from_dict(a) for a in d["actions"]],
                   tag_edits=[TagEdit.from_dict(t) for t in d.get("tag_edits", [])])


@dataclass
class JournalAction:
    action: Action
    status: str = PENDING

    def to_dict(self) -> dict:
        return {**self.action.to_dict(), "status": self.status}

    @classmethod
    def from_dict(cls, d: dict) -> JournalAction:
        return cls(action=Action.from_dict(d), status=d["status"])


@dataclass
class JournalTagEdit:
    """A tag write plus the OLD values it overwrote, so undo can restore them.
    A recorded ``None`` means the tag was absent (undo deletes it)."""

    path: Path
    old: dict
    new: dict
    status: str = PENDING

    def to_dict(self) -> dict:
        return {"path": str(self.path), "old": self.old, "new": self.new, "status": self.status}

    @classmethod
    def from_dict(cls, d: dict) -> JournalTagEdit:
        return cls(path=Path(d["path"]), old=d["old"], new=d["new"], status=d["status"])


@dataclass
class Journal:
    run_id: str
    library_root: Path
    actions: list[JournalAction]
    status: str = APPLYING
    created: str = ""
    tag_edits: list[JournalTagEdit] = field(default_factory=list)
    run_dir: Path | None = field(default=None, compare=False)

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "created": self.created, "status": self.status,
                "library_root": str(self.library_root),
                "actions": [a.to_dict() for a in self.actions],
                "tag_edits": [t.to_dict() for t in self.tag_edits]}

    @classmethod
    def from_dict(cls, d: dict) -> Journal:
        return cls(run_id=d["run_id"], created=d.get("created", ""), status=d["status"],
                   library_root=Path(d["library_root"]),
                   actions=[JournalAction.from_dict(a) for a in d["actions"]],
                   tag_edits=[JournalTagEdit.from_dict(t) for t in d.get("tag_edits", [])])


# --- path safety helpers (mirrors librarian.engine) ------------------------

def _norm(path: Path) -> str:
    """Case-folded, NFC-normalised absolute path for collision/containment."""
    return unicodedata.normalize("NFC", os.path.abspath(path)).casefold()


def _within(root: Path, path: Path) -> bool:
    root_n, path_n = _norm(root), _norm(path)
    return path_n == root_n or path_n.startswith(root_n + os.sep)


def _is_real_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _occupied(path: Path) -> bool:
    """True if ANYTHING sits at ``path`` — a file, a directory, or even a broken
    (dangling) symlink. Unlike ``Path.exists`` this does not follow links, so a
    symlink destination can never be silently clobbered."""
    return os.path.lexists(path)


def _nearest_existing(path: Path) -> Path:
    p = path
    while not os.path.lexists(p):
        if p.parent == p:
            return p
        p = p.parent
    return p


def _same_device(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_dev == _nearest_existing(b).stat().st_dev
    except OSError:
        return False


def _real(path: Path) -> Path:
    """Realpath of the nearest point on ``path`` that exists — follows every
    symlinked ancestor, so containment can't be fooled by a symlink in the tree."""
    return Path(os.path.realpath(_nearest_existing(path)))


def _contained(root_real: Path, path: Path) -> bool:
    """``path`` (resolved through symlinks) lives inside the resolved root."""
    return _within(root_real, _real(path))


def _safe_move(src: Path, dest: Path) -> None:
    """Atomically move ``src`` → ``dest`` without ever destroying data. Refuses
    anything occupying ``dest`` (file, dir, or dangling symlink) and any
    cross-volume move. All refusals happen BEFORE any filesystem change."""
    if not src.exists():
        raise OrganizeError(f"source missing, refusing to move: {src}")
    # A true case-only rename is the same logical path spelled differently — the
    # ONLY case where an occupied dest is acceptable. A hardlink/other-samefile
    # is NOT a case rename and must be refused, never temp-danced.
    case_rename = _norm(src) == _norm(dest)
    if _occupied(dest) and not case_rename:
        raise OrganizeError(f"destination already exists, refusing to overwrite: {dest}")
    if not _same_device(src, dest):
        raise OrganizeError(f"refusing cross-volume move (not an atomic rename): {src} -> {dest}")
    # Only now, after every refusal, may we touch the filesystem.
    dest.parent.mkdir(parents=True, exist_ok=True)
    if case_rename and _occupied(dest):
        # Case-only rename on a case-insensitive volume — go via a temp so the
        # bytes are never lost, then verify the temp was consumed.
        tmp = dest.with_name(dest.name + ".releases-rename-tmp")
        if _occupied(tmp):
            raise OrganizeError(f"rename temp already exists: {tmp}")
        os.rename(src, tmp)
        os.rename(tmp, dest)
        if _occupied(tmp):
            raise OrganizeError(f"rename temp not consumed (refusing to leave a stray): {tmp}")
        return
    os.rename(src, dest)


def _overlap(x: str, y: str) -> bool:
    """True if two normalised paths are equal or one contains the other."""
    return x == y or x.startswith(y + os.sep) or y.startswith(x + os.sep)


def preflight(plan: Plan) -> None:
    """Validate the WHOLE plan before any change — the boundary that keeps a
    reviewed/hand-edited JSON plan from escaping the library or clobbering."""
    root = plan.library_root
    if not _is_real_dir(root):
        raise OrganizeError(f"library root is not a directory: {root}")
    root_real = Path(os.path.realpath(root))
    seen: dict[str, Path] = {}
    seen_src: set[str] = set()
    for a in plan.actions:
        if not a.src.exists():
            raise OrganizeError(f"planned source does not exist: {a.src}")
        # Containment is checked lexically AND through symlinks (realpath), so a
        # symlinked directory in the tree can't redirect a move outside the root.
        if not (_within(root, a.src) and _contained(root_real, a.src)):
            raise OrganizeError(f"source escapes the library root {root}: {a.src}")
        if not (_within(root, a.dest) and _contained(root_real, a.dest)):
            raise OrganizeError(f"destination escapes the library root {root}: {a.dest}")
        if _occupied(a.dest) and _norm(a.src) != _norm(a.dest):
            raise OrganizeError(f"destination already exists, refusing to overwrite: {a.dest}")
        if not _same_device(a.src, a.dest):
            raise OrganizeError(f"cross-volume move refused: {a.src} -> {a.dest}")
        dkey = _norm(a.dest)
        if dkey in seen:
            raise OrganizeError(f"two actions target the same destination {a.dest}: {seen[dkey]} and {a.src}")
        seen[dkey] = a.src
        skey = _norm(a.src)
        if skey in seen_src:
            raise OrganizeError(f"two actions move the same source {a.src}")
        seen_src.add(skey)
    # Every action's src and dest must be pairwise disjoint from every other
    # action's src and dest (no equality, no nesting). That makes execution order
    # irrelevant and guarantees undo reverses cleanly — no half-moved tree.
    norm = [(_norm(a.src), _norm(a.dest), a) for a in plan.actions]
    for i, (s1, d1, a1) in enumerate(norm):
        for s2, d2, a2 in norm[i + 1:]:
            for p1, l1 in ((s1, "source"), (d1, "destination")):
                for p2, l2 in ((s2, "source"), (d2, "destination")):
                    if _overlap(p1, p2):
                        raise OrganizeError(
                            f"overlapping paths refused: {a1.src}'s {l1} overlaps {a2.src}'s {l2}"
                        )

    # Tag edits: validate every one before a byte is written, so the tag phase
    # is all-or-nothing-safe and a hand-edited plan can't write a forbidden tag
    # or touch a file outside the library.
    for t in plan.tag_edits:
        if not t.path.exists():
            raise OrganizeError(f"tag-edit source does not exist: {t.path}")
        if not (_within(root, t.path) and _contained(root_real, t.path)):
            raise OrganizeError(f"tag-edit path escapes the library root {root}: {t.path}")
        bad = [k for k in t.fields if k not in tags.WRITABLE_TAGS]
        if bad:
            raise OrganizeError(f"refusing to write non-writable tag(s) {bad} on {t.path}")
        if any(not isinstance(v, (str, type(None))) for v in t.fields.values()):
            raise OrganizeError(f"tag values must be strings or null: {t.path}")
        if not tags.is_taggable(t.path):
            raise OrganizeError(f"file cannot carry tags, refusing to retag: {t.path}")


def new_run_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"


def write_journal(journal: Journal) -> None:
    """Atomically persist the journal (temp + fsync + os.replace + dir fsync)."""
    if journal.run_dir is None:
        raise ValueError("journal.run_dir must be set before writing")
    journal.run_dir.mkdir(parents=True, exist_ok=True)
    path = journal.run_dir / JOURNAL_NAME
    tmp = path.with_suffix(f".json.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(json.dumps(journal.to_dict(), indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(journal.run_dir, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def load_journal(run_dir: Path) -> Journal:
    data = json.loads((run_dir / JOURNAL_NAME).read_text(encoding="utf-8"))
    j = Journal.from_dict(data)
    j.run_dir = run_dir
    return j


def list_runs(runs_dir: Path) -> list[Journal]:
    if not runs_dir.is_dir():
        return []
    out = []
    for child in runs_dir.iterdir():
        if (child / JOURNAL_NAME).exists():
            try:
                out.append(load_journal(child))
            except Exception:
                continue
    out.sort(key=lambda j: j.run_id, reverse=True)
    return out


# --- planning: turn index decisions into a folder Plan --------------------

# The canonical on-disk home for each taxonomy stage, under Beats/Tracks/.
# Pipeline stages (released/scheduled) and uncategorized have no home, so a
# project in one of those is never auto-filed.
STAGE_FOLDERS: dict[str, str] = {
    "complete": "Complete Tracks",
    "need-arranged": "Need Arranged : Deep Progress",
    "remix": "Remixes In Progress",
    "return-to": "Return to",
    "mels": "Mels",
    "bones": "Project bones",
    "track-list": "Track List",
}


def _stage_dest(root: Path, stage: str, name: str) -> Path:
    return root / "Beats" / "Tracks" / STAGE_FOLDERS[stage] / name


def plan_file(root: Path, src: Path, stage: str, reason: str) -> tuple[Action | None, str | None]:
    """Plan to file ``src`` into ``stage``'s folder. Returns (action, skip-note)
    — at most one is non-None."""
    if stage not in STAGE_FOLDERS:
        return None, f"{src.name}: stage {stage!r} has no canonical folder"
    dest = _stage_dest(root, stage, src.name)
    if _norm(src) == _norm(dest):
        return None, f"{src.name}: already filed under {STAGE_FOLDERS[stage]}"
    if _occupied(dest):
        return None, f"{src.name}: {STAGE_FOLDERS[stage]}/{src.name} already exists — skipped"
    return Action(MOVE, src, dest, reason, stage=stage), None


def plan_by_stage(root: Path, projects) -> tuple[list[Action], list[str]]:
    """Propose moves so each project's folder sits in its *effective* stage's
    home. Projects already filed, or in a non-fileable stage, are skipped (noted).
    Destination collisions are skipped, never overwritten."""
    actions: list[Action] = []
    notes: list[str] = []
    taken: set[str] = set()
    for p in projects:
        stage = p.effective_stage
        src = Path(p.path)
        action, note = plan_file(root, src, stage, f"effective stage is {stage}")
        if action is None:
            if note and stage in STAGE_FOLDERS:  # only note genuine non-filed cases worth seeing
                notes.append(note)
            continue
        dkey = _norm(action.dest)
        if dkey in taken:
            notes.append(f"{src.name}: another project already claims that destination — skipped")
            continue
        taken.add(dkey)
        actions.append(action)
    return actions, notes


def plan_rename(root: Path, src: Path, new_name: str) -> tuple[Action | None, str | None]:
    """Plan to rename a project folder (or loose file, keeping its extension)."""
    if "/" in new_name or "\\" in new_name or new_name in ("", ".", ".."):
        return None, f"invalid name {new_name!r}"
    if src.is_dir():
        dest = src.parent / new_name
    else:
        dest = src.parent / (new_name + src.suffix)
    if _norm(src) == _norm(dest):
        return None, f"{src.name}: name unchanged"
    if _occupied(dest):
        return None, f"{dest.name} already exists — skipped"
    return Action(RENAME, src, dest, f"rename to {new_name}"), None


def _safe_component(name: str) -> str:
    """A single, safe path component — no separators or traversal."""
    cleaned = name.replace("/", "-").replace("\\", "-").strip()
    if cleaned in ("", ".", ".."):
        return "Unknown"
    return cleaned


def _title_genre(genre: str) -> str:
    return _safe_component(genre.title()) if genre and genre != "unknown" else "Unknown"


def plan_tracklist(root: Path, marks_by_path: dict) -> tuple[list[Action], list[TagEdit], list[str]]:
    """Plan to file every Track List audio file into
    ``Track List/<Genre>/<mix>/<master>/`` AND stamp it with a genre + a
    'mix / master' comment tag. ``marks_by_path`` maps an absolute file path to
    its indexed Project (for genre/mix/master marks); files not in the index get
    the defaults (Unknown / unmixed / unmastered).

    Idempotent: a file already sitting in a ``<Genre>/<mix>/<master>/`` leaf is
    left alone unless an explicit mark says it belongs elsewhere — so re-runs on
    a converged library produce an empty plan and a lost mark never drags a
    curated track back to Unknown. Tag edits are emitted only for files that
    move, so nothing is re-tagged needlessly."""
    tl = root / "Beats" / "Tracks" / "Track List"
    if not tl.is_dir():
        return [], [], [f"no Track List folder at {tl}"]
    moves: list[Action] = []
    tag_edits: list[TagEdit] = []
    notes: list[str] = []
    seen_dest: set[str] = set()
    for dirpath, _dirs, files in os.walk(tl):
        here = Path(dirpath)
        # A bounce that lives beside a .flp belongs to a project folder — moving
        # it would orphan it from its project, so leave the whole folder alone.
        if any(name.lower().endswith(".flp") for name in files):
            continue
        for f in sorted(files):
            src = here / f
            if src.suffix.lower() not in AUDIO_EXTS:
                continue
            rel_parts = src.relative_to(tl).parts
            p = marks_by_path.get(str(src))
            has_mark = bool(p and (p.genre_manual or p.mix_state or p.master_state))
            genre = p.effective_genre if p else "unknown"
            mix = p.effective_mix if p else "unmixed"
            master = p.effective_master if p else "unmastered"
            gfolder = _title_genre(genre)
            desired = (gfolder, mix, master)

            # Already inside a <genre>/<mix>/<master>/<name> leaf?
            if len(rel_parts) == 4:
                current = tuple(rel_parts[:3])
                if current == desired or not has_mark:
                    continue  # converged, or don't disturb a curated placement

            dest = tl / gfolder / mix / master / src.name
            if _norm(src) == _norm(dest):
                continue
            if _occupied(dest) or _norm(dest) in seen_dest:
                notes.append(f"{src.name}: target {gfolder}/{mix}/{master} occupied — move skipped")
                continue
            seen_dest.add(_norm(dest))
            moves.append(Action(MOVE, src, dest, f"file under {gfolder}/{mix}/{master}"))
            # Tag only files we actually move, so a converged library re-tags nothing.
            if tags.is_taggable(src):
                fields = {"comment": f"{mix} / {master}"}
                if genre != "unknown":
                    fields["genre"] = gfolder
                tag_edits.append(TagEdit(src, fields, f"{gfolder} · {mix} · {master}"))
    return moves, tag_edits, notes


def apply_plan(plan: Plan, runs_dir: Path) -> Journal:
    """Execute a reviewed plan, journaling every step. Returns the Journal."""
    preflight(plan)
    run_id = new_run_id()
    run_dir = runs_dir / run_id
    journal = Journal(
        run_id=run_id,
        library_root=plan.library_root.absolute(),
        actions=[JournalAction(action=a, status=PENDING) for a in plan.actions],
        tag_edits=[
            JournalTagEdit(path=t.path.absolute(), old={}, new=dict(t.fields), status=PENDING)
            for t in plan.tag_edits
        ],
        status=APPLYING,
        created=run_id[:15],
        run_dir=run_dir,
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    write_journal(journal)  # journal-before-execute

    # Tag edits run first, on the files' current (pre-move) paths. Read the OLD
    # values and journal them BEFORE writing, so a crash still leaves undo able
    # to restore exactly what was there.
    for tedit in journal.tag_edits:
        try:
            tedit.old = tags.read_tags(tedit.path, list(tedit.new.keys()))
            write_journal(journal)
            tags.write_tags(tedit.path, tedit.new)
        except Exception as e:
            write_journal(journal)
            raise ApplyInterrupted(f"tag write failed at {tedit.path}: {e}", journal) from e
        tedit.status = DONE
        write_journal(journal)

    for entry in journal.actions:
        try:
            _safe_move(entry.action.src, entry.action.dest)
        except (OrganizeError, OSError) as e:
            # The completed moves are journaled (DONE) and intact; surface the
            # partial journal so the caller can re-link the index and undo.
            write_journal(journal)
            raise ApplyInterrupted(
                f"move failed at {entry.action.src} -> {entry.action.dest}: {e}", journal
            ) from e
        entry.status = DONE
        write_journal(journal)
    journal.status = APPLIED
    write_journal(journal)
    return journal


def undo_run(run_id: str, runs_dir: Path) -> Journal:
    """Reverse a run completely: every completed move back, newest first."""
    if not run_id or "/" in run_id or "\\" in run_id or run_id in (".", ".."):
        raise OrganizeError(f"invalid run id: {run_id!r}")
    run_dir = runs_dir / run_id
    if run_dir.resolve().parent != runs_dir.resolve():
        raise OrganizeError(f"invalid run id (escapes runs dir): {run_id!r}")
    if not (run_dir / JOURNAL_NAME).exists():
        raise OrganizeError(f"no run found with id {run_id} under {runs_dir}")
    journal = load_journal(run_dir)
    if journal.status == UNDONE:
        raise OrganizeError(f"run {run_id} has already been undone")
    root = journal.library_root
    root_real = Path(os.path.realpath(root)) if root.exists() else root
    for entry in journal.actions:
        if entry.status == DONE and not (
            _within(root, entry.action.src) and _within(root, entry.action.dest)
            and _contained(root_real, entry.action.dest)
        ):
            raise OrganizeError(f"refusing undo: action escapes library root {root}: {entry.action.src}")
    for tedit in journal.tag_edits:
        if tedit.status == DONE and not _within(root, tedit.path):
            raise OrganizeError(f"refusing undo: tag-edit path escapes library root {root}: {tedit.path}")
    # Mark mid-reversal before the first move back, so a crashed undo is visible.
    journal.status = UNDOING
    write_journal(journal)
    for entry in reversed(journal.actions):
        if entry.status == DONE:
            _safe_move(entry.action.dest, entry.action.src)
            entry.status = REVERTED
            write_journal(journal)
    # Files are back at their original paths, so restore tags to the pre-run
    # values the journal captured (a recorded None deletes a tag that was absent).
    for tedit in reversed(journal.tag_edits):
        if tedit.status == DONE:
            tags.write_tags(tedit.path, tedit.old)
            tedit.status = REVERTED
            write_journal(journal)
    journal.status = UNDONE
    write_journal(journal)
    return journal
