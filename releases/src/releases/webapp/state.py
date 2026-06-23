"""Launch-time config + per-process state for the web app.

The library root and db are fixed when the server starts (``releases web``) — no
endpoint accepts a library/db path from the client, so the app can't be pointed
elsewhere by injection. Track files are addressed by an opaque id mapped
server-side to an absolute path (never a client-supplied path); the organize Plan
is cached server-side and applied by id, so client input only ever indexes the
server's own authoritative state.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from ..organize import Plan

# Bound the server-side plan cache so a long session can't grow memory unbounded.
MAX_CACHED_PLANS = 16


@dataclass
class AppConfig:
    library_root: Path
    db_path: Path
    runs_dir: Path
    port: int = 8765
    producer: str = "Dibs"   # stamped on exported packs
    contact: str = ""
    cover_src: Path | None = None  # default cover art for exported pack labels
    stems_dir: Path | None = None  # where separated stems are cached (default: beside the db)

    def __post_init__(self) -> None:
        # .resolve() to match scan(), which stores resolved absolute paths in the
        # index — so a path reconstructed here keys the same projects row.
        self.library_root = self.library_root.resolve()
        self.db_path = self.db_path.absolute()
        self.runs_dir = self.runs_dir.absolute()
        self.stems_dir = (self.stems_dir or self.db_path.parent / "stems").absolute()

    @property
    def track_list_dir(self) -> Path:
        return self.library_root / "Beats" / "Tracks" / "Track List"


@dataclass
class AppState:
    config: AppConfig
    # opaque track id -> absolute path (rebuilt on each /api/tracks call)
    track_paths: dict[str, str] = field(default_factory=dict)
    # plan id -> cached organize Plan awaiting apply
    plans: "OrderedDict[str, Plan]" = field(default_factory=OrderedDict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def cache_plan(self, plan_id: str, plan: Plan) -> None:
        with self.lock:
            self.plans[plan_id] = plan
            while len(self.plans) > MAX_CACHED_PLANS:
                self.plans.popitem(last=False)

    def get_plan(self, plan_id: str) -> Plan | None:
        with self.lock:
            return self.plans.get(plan_id)
