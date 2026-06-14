"""Shared fixtures: a synthetic ProducerLibrary mirroring the real taxonomy.

The synthetic tree exercises every scan branch — multi-version folder-projects,
sample-pack noise that must be pruned, standalone bounces, BPM/key in names —
without depending on the real (private) library. A separate fixture points at
the real library and skips cleanly when it isn't present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REAL_LIBRARY = Path.home() / "ProducerLibrary" / "projects"


def _touch(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.fixture
def synth_library(tmp_path: Path) -> Path:
    """A miniature projects/ tree covering the cases the scanner must handle."""
    root = tmp_path / "projects"
    tracks = root / "Beats" / "Tracks"

    # folder-project, multi-version, with a sibling bounce → complete, has_bounce
    _touch(tracks / "Complete Tracks" / "Song" / "Encara" / "Encara_129_F_Maj.flp")
    _touch(tracks / "Complete Tracks" / "Song" / "Encara" / "Encara_129_F_Maj_2.flp")
    _touch(tracks / "Complete Tracks" / "Song" / "Encara" / "Encara final.mp3")

    # folder-project, no bounce → need-arranged, BPM/key in folder name
    _touch(tracks / "Need Arranged : Deep Progress" / "joonya 137 Cmin" / "joonya.flp")

    # folder-project with stems in Audio/ (must NOT count as a bounce)
    _touch(tracks / "Mels" / "sketch1" / "sketch1.flp")
    _touch(tracks / "Mels" / "sketch1" / "Audio" / "vocals.wav")

    # folder-project with an export-named subfolder (counts as a bounce)
    _touch(tracks / "Remixes In Progress" / "rmx1" / "rmx1.flp")
    _touch(tracks / "Remixes In Progress" / "rmx1" / "exports" / "rmx1 master.wav")

    # Return to / Potential — should outrank "If really bored"
    _touch(tracks / "Return to" / "Potential" / "goodone" / "goodone.flp")
    _touch(tracks / "Return to" / "If really bored" / "meh" / "meh.flp")

    # standalone bounce in a stage folder (no .flp beside it)
    _touch(tracks / "Track List" / "Cha Cha Slide (Dibs)_140_Dmin.mp3")

    # standalone .flp at the projects root
    _touch(root / "PinkPanther.flp")

    # --- noise that must be pruned ---
    _touch(root / "Beats" / "Drum Kits" / "Rap" / "kit" / "demo.flp")  # sample pack
    _touch(root / "Beats" / "Diva Presets" / "pack" / "demo.flp")       # preset pack
    _touch(tracks / "Complete Tracks" / "Song" / "Encara" / "Backup" / "Encara_autosave.flp")

    return root


@pytest.fixture
def real_library() -> Path:
    if not REAL_LIBRARY.exists():
        pytest.skip("real ProducerLibrary not present")
    return REAL_LIBRARY
