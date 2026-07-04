"""Launch the web app with uvicorn, bound to localhost only."""

from __future__ import annotations

from pathlib import Path

from ..paths import default_runs_dir
from .app import create_app
from .state import AppConfig


def serve(
    library_root: Path,
    *,
    rekordbox_xml: Path | None = None,
    runs_dir: Path | None = None,
    port: int = 8765,
) -> None:
    import uvicorn

    config = AppConfig(
        library_root=library_root,
        # Outside the library, shared with the CLI — so run backups never pollute
        # the tree and every entry point's runs land in one place (paths.default_runs_dir).
        runs_dir=runs_dir or default_runs_dir(library_root),
        rekordbox_xml=rekordbox_xml,
        port=port,
    )
    app = create_app(config)
    # 127.0.0.1 only — never 0.0.0.0. The app writes to the library; it must not
    # be reachable from the network.
    uvicorn.run(app, host="127.0.0.1", port=port)
