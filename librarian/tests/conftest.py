"""Shared fixtures + helpers for the engine tests."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

# The real testbed copy the brief insists we prove against. Tests that use it
# skip cleanly if it isn't present, so the suite stays portable.
SAMPLE_LIBRARY = Path.home() / "dev" / "librarian-testbed" / "sample-library"


def tree_digest(root: Path, *, ignore: set[str] = frozenset()) -> dict[str, str]:
    """Map every file under ``root`` (by relative path) to its sha256.

    Directories named in ``ignore`` are skipped wholesale — used to exclude the
    run/journal directory from a library round-trip comparison.
    """
    digest: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ignore]
        for name in filenames:
            p = Path(dirpath) / name
            rel = str(p.relative_to(root))
            digest[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return digest


def make_library(root: Path, names: list[str]) -> dict[str, bytes]:
    """Create ``names`` under ``root`` with distinct, recognisable contents."""
    root.mkdir(parents=True, exist_ok=True)
    contents: dict[str, bytes] = {}
    for i, name in enumerate(names):
        data = f"audio-bytes-for-{name}-{i}".encode()
        (root / name).write_bytes(data)
        contents[name] = data
    return contents


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """A small synthetic library with junk names + a duplicate copy marker."""
    root = tmp_path / "lib"
    make_library(
        root,
        [
            "Chammak Challo_spotdown.org.mp3",
            "Avicii - The Nights (Clean Extended).mp3",
            "Burna Boy ft Travis Scott - TaTaTa (Intro Dirty).mp3",
            "BabyChiefDoIt - WENT WEST (Intro Dirty) (1).mp3",
        ],
    )
    return root


@pytest.fixture
def runs_dir(tmp_path: Path) -> Path:
    """Journals live OUTSIDE the library so they never pollute a round-trip."""
    return tmp_path / "runs"
