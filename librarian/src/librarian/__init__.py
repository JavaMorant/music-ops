"""librarian — a reversible plan/apply/undo engine for the DJ music library.

Safety invariants (non-negotiable — this tool writes to the library):
  * Dry-run by default: a plan is produced and reviewed before anything moves.
  * Nothing is ever deleted — duplicates and rejects go to quarantine.
  * Every change is journaled to disk *before* it executes, so an interrupted
    run is always recoverable and fully reversible via ``librarian undo``.
  * Files rekordbox tracks keep their cues: moves rewrite the rekordbox XML.
"""

__version__ = "0.1.0"
