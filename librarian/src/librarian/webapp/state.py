"""Launch-time config and per-process state for the web app.

The library root is fixed when the server starts (``librarian serve <root>``) —
no endpoint ever accepts a library path from the client, so the app can't be
pointed at the real library by accident or injection. The built Plan is cached
server-side and applied by id, so client input is only ever ``keep_ids`` indexing
the server's own authoritative plan.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..model import Plan

# A generous ceiling for a single dropped file (the user's real WAV sets run to
# a few hundred MB each); rejected above this, never silently truncated.
DEFAULT_MAX_UPLOAD_BYTES = 500 * 1024 * 1024


@dataclass
class AppConfig:
    library_root: Path
    runs_dir: Path
    rekordbox_xml: Path | None = None
    inbox_dir: Path | None = None
    port: int = 8765
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES

    def __post_init__(self) -> None:
        # .absolute(), NOT .resolve(): the engine, planner and rekordbox matching
        # all key off os.path.abspath (no symlink resolution). If we resolved
        # symlinks here, a library reached via a symlink (e.g. ~/Music -> an
        # external drive) would make every action path diverge from the rekordbox
        # Locations, silently leaving them as dead pointers. Match the engine.
        self.library_root = self.library_root.absolute()
        self.runs_dir = self.runs_dir.absolute()
        if self.rekordbox_xml is not None:
            self.rekordbox_xml = self.rekordbox_xml.absolute()
        self.inbox_dir = (self.inbox_dir or self.library_root / "Inbox").absolute()


@dataclass
class AppState:
    """Mutable per-process state hung off ``app.state.librarian``."""

    config: AppConfig
    plans: dict[str, Plan] = field(default_factory=dict)
    # Cached metadata-dedupe analysis (slow to build); invalidated after an apply.
    dedupe: dict | None = None
    # Serializes apply/undo so two overlapping calls can't race the journal.
    apply_lock: threading.Lock = field(default_factory=threading.Lock)
