"""Sidecar for per-set custom names + linked recordings.

The stick history auto-names sets 'DL+ set #137' etc., so user-given names and
the path to the Opus recording for each set live here, keyed by the stable set
key from pulse_usb.stick_sets.
"""
from __future__ import annotations

import json
from pathlib import Path

STORE = Path.home() / "DJ" / ".librarian" / "sets.json"


def load() -> dict:
    if STORE.exists():
        try:
            return json.loads(STORE.read_text())
        except Exception:
            return {}
    return {}


def save_one(key: str, name: str | None = None, recording: str | None = None) -> dict:
    data = load()
    entry = data.get(key, {})
    if name is not None:
        entry["name"] = name.strip()
    if recording is not None:
        entry["recording"] = recording.strip()
    data[key] = entry
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    return entry
