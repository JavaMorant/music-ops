"""The releases local web app — a thin FastAPI + single-page UI for managing the
Track List: set genre / mix / master, play tracks, then file + tag them through
the SAME organize plan/apply/undo engine the CLI uses.

It adds no new mutation path: marking goes through ``db.set_marks`` (index-only),
organize goes through ``organize.apply_plan`` / ``undo_run``, so every safety
invariant (dry-run review, never-overwrite, journaled, reversible) is inherited.
The library root is fixed when the server starts — no endpoint accepts a library
path from the client. See ``server.serve`` and ``app.create_app``.
"""
