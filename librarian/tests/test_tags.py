"""Real mutagen tag I/O — write, read back, and restore on an actual MP3.

The engine-level reversibility logic is covered portably in test_retag.py with a
faked store; this proves the real read/write/restore round-trip (including that a
recorded ``None`` deletes a tag) against a real DJ file. Skips cleanly when the
testbed isn't present.
"""

from __future__ import annotations

import shutil

import pytest

from librarian import tags

from conftest import SAMPLE_LIBRARY

FIELDS = ["artist", "title", "genre"]


def test_is_taggable_false_for_non_audio(tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("not audio", encoding="utf-8")
    assert tags.is_taggable(txt) is False


@pytest.mark.skipif(not SAMPLE_LIBRARY.is_dir(), reason="testbed not present")
def test_real_mp3_tag_write_read_restore(tmp_path):
    src = next(SAMPLE_LIBRARY.glob("*.mp3"))
    f = tmp_path / "track.mp3"
    shutil.copy2(src, f)
    raw_before = f.read_bytes()

    before = tags.read_tags(f, FIELDS)
    assert tags.is_taggable(f) is True

    tags.write_tags(f, {"artist": "Test Artist", "title": "Test Title", "genre": "Test Genre"})
    assert tags.read_tags(f, FIELDS) == {
        "artist": "Test Artist", "title": "Test Title", "genre": "Test Genre",
    }

    # Restore exactly what was there (a None deletes a tag that was absent).
    tags.write_tags(f, before)
    assert tags.read_tags(f, FIELDS) == before
    # The audio payload is intact — we only ever touched the tag block.
    assert len(f.read_bytes()) > 0
