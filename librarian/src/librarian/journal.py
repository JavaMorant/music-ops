"""The undo journal: a durable, on-disk record of what a run intends to do and
what it has done so far.

The journal is written *before* the first file moves and flushed after every
single move, each write atomic (temp file + ``os.replace``) and fsync'd. So at
any instant — including a crash or kill mid-run — the journal on disk is a true
account of the library's state, and ``librarian undo`` can reverse exactly the
moves that completed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .model import Action

# Per-action lifecycle.
PENDING = "pending"   # journaled, not yet executed
DONE = "done"         # executed; src -> dest happened
REVERTED = "reverted"  # undo moved dest back to src

# Per-run lifecycle.
APPLYING = "applying"  # mid-apply (this is the state on disk while moves run)
APPLIED = "applied"    # every action done
UNDONE = "undone"      # every done action reverted

JOURNAL_NAME = "journal.json"


@dataclass
class JournalAction:
    """An Action plus its execution status within a run."""

    action: Action
    status: str = PENDING

    def to_dict(self) -> dict:
        return {**self.action.to_dict(), "status": self.status}

    @classmethod
    def from_dict(cls, data: dict) -> JournalAction:
        return cls(action=Action.from_dict(data), status=data["status"])


@dataclass
class Journal:
    """The full record of one apply run, persisted to ``<run_dir>/journal.json``."""

    run_id: str
    library_root: Path
    actions: list[JournalAction]
    status: str = APPLYING
    created: str = ""
    # rekordbox bookkeeping: original XML path + backup copy, so undo can restore
    # the collection byte-for-byte. None when the run touched no rekordbox XML.
    rekordbox: dict | None = None
    # Pre-apply backup bookkeeping (dir, mode, file count). None if skipped.
    backup: dict | None = None
    run_dir: Path | None = field(default=None, compare=False)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "created": self.created,
            "status": self.status,
            "library_root": str(self.library_root),
            "rekordbox": self.rekordbox,
            "backup": self.backup,
            "actions": [a.to_dict() for a in self.actions],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Journal:
        return cls(
            run_id=data["run_id"],
            created=data.get("created", ""),
            status=data["status"],
            library_root=Path(data["library_root"]),
            rekordbox=data.get("rekordbox"),
            backup=data.get("backup"),
            actions=[JournalAction.from_dict(a) for a in data["actions"]],
        )


def new_run_id() -> str:
    """A sortable, unique run id: ``YYYYmmdd-HHMMSS-<6 hex>``."""
    return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"


def write_journal(journal: Journal) -> None:
    """Atomically persist the journal to ``<run_dir>/journal.json``.

    Writes a sibling temp file, fsyncs it, then ``os.replace``s it into place —
    so a reader never sees a half-written journal, and the rename is durable.
    """
    if journal.run_dir is None:
        raise ValueError("journal.run_dir must be set before writing")
    journal.run_dir.mkdir(parents=True, exist_ok=True)
    path = journal.run_dir / JOURNAL_NAME
    # Per-process temp name so a stale temp from a crashed run isn't shared.
    tmp = path.with_suffix(f".json.{os.getpid()}.tmp")
    data = json.dumps(journal.to_dict(), indent=2)
    with tmp.open("w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    # fsync the directory so the rename itself is durable across a power loss.
    dir_fd = os.open(journal.run_dir, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def load_journal(run_dir: Path) -> Journal:
    path = run_dir / JOURNAL_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    journal = Journal.from_dict(data)
    journal.run_dir = run_dir
    return journal


def list_runs(runs_dir: Path) -> list[Journal]:
    """All runs under ``runs_dir``, newest first. Missing dir → empty list."""
    if not runs_dir.is_dir():
        return []
    journals = []
    for child in runs_dir.iterdir():
        if (child / JOURNAL_NAME).exists():
            journals.append(load_journal(child))
    journals.sort(key=lambda j: j.run_id, reverse=True)
    return journals
