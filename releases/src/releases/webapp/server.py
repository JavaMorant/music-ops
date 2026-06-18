"""Launch the web app with uvicorn, bound to localhost only."""

from __future__ import annotations

from pathlib import Path

from .app import create_app
from .state import AppConfig


def serve(library_root: Path, db_path: Path, runs_dir: Path, port: int = 8765) -> None:
    import uvicorn

    config = AppConfig(library_root=library_root, db_path=db_path, runs_dir=runs_dir, port=port)
    app = create_app(config)
    # 127.0.0.1 only — never 0.0.0.0. The app can write to the library; it must
    # not be reachable from the network.
    uvicorn.run(app, host="127.0.0.1", port=port)
