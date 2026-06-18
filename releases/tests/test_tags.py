"""ID3 tag read/write round-trip (mutagen). Uses a dummy .mp3 — mutagen writes
an ID3v2 tag at the head of any file, so no real audio frames are needed."""

from __future__ import annotations

import pytest

from releases import tags


def _mp3(tmp_path, name="t.mp3"):
    p = tmp_path / name
    p.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 256)  # minimal frame-ish bytes
    return p


def test_is_taggable():
    from pathlib import Path
    assert tags.is_taggable(Path("a.mp3"))
    assert tags.is_taggable(Path("A.MP3"))
    assert not tags.is_taggable(Path("a.wav"))


def test_write_then_read(tmp_path):
    p = _mp3(tmp_path)
    tags.write_tags(p, {"genre": "Jersey Club", "comment": "mixed / mastered"})
    got = tags.read_tags(p, ["genre", "comment"])  # values come back as full lists
    assert got == {"genre": ["Jersey Club"], "comment": ["mixed / mastered"]}


def test_absent_tags_read_as_none(tmp_path):
    p = _mp3(tmp_path)
    assert tags.read_tags(p, ["genre", "comment"]) == {"genre": None, "comment": None}


def test_none_value_deletes_the_tag(tmp_path):
    p = _mp3(tmp_path)
    tags.write_tags(p, {"genre": "Afro", "comment": "x"})
    tags.write_tags(p, {"genre": None, "comment": None})  # undo-style restore-to-absent
    assert tags.read_tags(p, ["genre", "comment"]) == {"genre": None, "comment": None}


def test_sibling_comment_frames_are_preserved(tmp_path):
    """Regression (critical): writing our comment must not wipe other COMM
    frames (iTunNORM, other-language comments)."""
    import mutagen.id3 as id3
    p = _mp3(tmp_path)
    tag = id3.ID3()
    tag.add(id3.COMM(encoding=3, lang="eng", desc="iTunNORM", text=["00000A 00000B"]))
    tag.add(id3.COMM(encoding=3, lang="deu", desc="", text=["Deutscher Kommentar"]))
    tag.save(p)

    tags.write_tags(p, {"comment": "mixed / mastered"})  # write OUR comment
    after = id3.ID3(p)
    assert after.get("COMM::eng").text == ["mixed / mastered"]   # ours set
    assert after.get("COMM:iTunNORM:eng").text == ["00000A 00000B"]  # sibling survives
    assert after.get("COMM::deu").text == ["Deutscher Kommentar"]    # other lang survives


def test_full_apply_undo_keeps_siblings_and_restores_value(tmp_path):
    import mutagen.id3 as id3
    p = _mp3(tmp_path)
    tag = id3.ID3()
    tag.add(id3.COMM(encoding=3, lang="eng", desc="iTunNORM", text=["NORMDATA"]))
    tag.add(id3.COMM(encoding=3, lang="eng", desc="", text=["original human note"]))
    tag.save(p)

    old = tags.read_tags(p, ["comment"])            # journal step
    assert old == {"comment": ["original human note"]}  # the RIGHT frame, not iTunNORM
    tags.write_tags(p, {"comment": "mixed / mastered"})
    tags.write_tags(p, old)                          # undo step
    after = id3.ID3(p)
    assert after.get("COMM::eng").text == ["original human note"]   # restored exactly
    assert after.get("COMM:iTunNORM:eng").text == ["NORMDATA"]      # sibling intact


def test_multivalue_genre_round_trips(tmp_path):
    p = _mp3(tmp_path)
    import mutagen.id3 as id3
    tag = id3.ID3()
    tag.add(id3.TCON(encoding=3, text=["Hip-Hop", "Rap"]))
    tag.save(p)
    old = tags.read_tags(p, ["genre"])
    assert old == {"genre": ["Hip-Hop", "Rap"]}      # both values journaled
    tags.write_tags(p, {"genre": "Jersey Club"})
    tags.write_tags(p, old)                           # undo restores the full list
    assert tags.read_tags(p, ["genre"]) == {"genre": ["Hip-Hop", "Rap"]}


def test_refuses_unwritable_field(tmp_path):
    p = _mp3(tmp_path)
    with pytest.raises(ValueError, match="not writable"):
        tags.write_tags(p, {"artist": "x"})
