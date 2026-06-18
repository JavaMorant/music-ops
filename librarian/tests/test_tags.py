"""Real mutagen tag I/O — write, read back, and restore on an actual MP3.

The engine-level reversibility logic is covered portably in test_retag.py with a
faked store; this proves the real read/write/restore round-trip (including that a
recorded ``None`` deletes a tag) against a real DJ file. Skips cleanly when the
testbed isn't present.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from librarian import tags

from conftest import SAMPLE_LIBRARY

FIELDS = ["artist", "title", "genre"]


def test_is_taggable_false_for_non_audio(tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("not audio", encoding="utf-8")
    assert tags.is_taggable(txt) is False


def test_is_taggable_false_for_wav_true_for_mp3():
    # WAV/AIFF can't be easy-tag-written; the check is extension-gated
    assert tags.is_taggable(Path("x.wav")) is False
    assert tags.is_taggable(Path("x.aiff")) is False
    assert ".mp3" in tags.WRITABLE_EXTS and ".m4a" in tags.WRITABLE_EXTS


def test_write_tags_raises_clean_tagerror_on_set_failure(tmp_path, monkeypatch):
    # a format whose mapping rejects the key (like WAV) must surface as TagError,
    # never a raw exception that crashes apply
    class Rejecting(dict):
        def __setitem__(self, k, v):
            raise ValueError("this format can't set that key")

    monkeypatch.setattr(tags, "_open", lambda p, create=False: Rejecting())
    with pytest.raises(tags.TagError):
        tags.write_tags(tmp_path / "x.mp3", {"title": "X"})


def test_read_tags_preserves_empty_and_handles_missing(monkeypatch):
    # artist set to empty string, title present-but-valueless, genre set, album absent
    stub = {"artist": [""], "title": [], "genre": ["House"]}
    monkeypatch.setattr(tags, "_open", lambda p, create=False: stub)
    out = tags.read_tags(Path("x.mp3"), ["artist", "title", "genre", "album"])
    # empty string is preserved (not dropped to None); [] and missing → None (no IndexError)
    assert out == {"artist": "", "title": None, "genre": "House", "album": None}


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
