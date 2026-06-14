"""The librarian local web app — a thin FastAPI + single-page UI over the SAME
plan/apply/undo engine the CLI uses. It adds no mutation path: handlers only call
the existing builders, ``select_actions``, ``apply_plan``, ``undo_run`` and
``list_runs``, so every safety invariant is inherited. See ``app.create_app``.
"""
