"""releases — turn idle ProducerLibrary projects into a shipping schedule.

A read-only scanner over ``~/ProducerLibrary/projects`` that infers each
project's stage from the folder taxonomy, parses BPM/key/genre from names,
and ranks the ones nearest done — then plans a single-every-N-weeks calendar
toward an EP deadline.

Safety: the library is **never** written to. The scan only reads; all state
(the SQLite index, the schedule, the status log) lives in a separate db file
outside the library.
"""

__version__ = "0.1.0"
