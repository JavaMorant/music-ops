"""Pack export tests — clean naming, the player page + tracklist, dedupe, and
that the library (sources) is never modified."""

from __future__ import annotations

from pathlib import Path

from releases import pack as packmod


def _track(tmp_path, fname, title, bpm=None, key=None, genre="unknown", artists=""):
    src = tmp_path / "lib" / fname
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"\xff\xfb\x90\x00" + b"audio")
    return packmod.PackTrack(src=src, title=title, bpm=bpm, key=key, genre=genre, artists=artists)


def _meta(name="Trap Pack"):
    return packmod.PackMeta(name=name, producer="Dibs", made_on="2026-06-20", contact="dibs@x.com")


def test_clean_track_filename():
    t = packmod.PackTrack(src=Path("/x/a.mp3"), title="My Beat", bpm=140, key="Fm", genre="trap", artists="")
    assert packmod.clean_track_filename(1, t) == "01 - My Beat [140 Fm].mp3"
    t2 = packmod.PackTrack(src=Path("/x/b.mp3"), title="No Meta", bpm=None, key=None, genre="unknown", artists="")
    assert packmod.clean_track_filename(2, t2) == "02 - No Meta.mp3"


def test_safe_filename_strips_separators():
    assert "/" not in packmod.safe_filename("a/b\\c") and "\\" not in packmod.safe_filename("a/b\\c")


def test_build_pack_writes_audio_player_and_tracklist(tmp_path):
    tracks = [
        _track(tmp_path, "x.mp3", "Encara", bpm=129, key="F", genre="jersey club"),
        _track(tmp_path, "y.mp3", "Gqom Thing", bpm=150, key="Gm", genre="gqom", artists="Jah"),
    ]
    out = tmp_path / "out" / "Trap Pack"
    names = packmod.build_pack(tracks, out, _meta())
    assert names == ["01 - Encara [129 F].mp3", "02 - Gqom Thing [150 Gm].mp3"]
    for n in names:
        assert (out / n).exists()
    idx = (out / "index.html").read_text()
    assert "Encara" in idx and "Produced by" in idx and "Dibs" in idx
    assert "01 - Encara [129 F].mp3" in idx  # the audio is wired into the player
    tl = (out / "tracklist.txt").read_text()
    assert "Encara" in tl and "150 BPM" in tl and "feat. Jah" in tl


def test_build_pack_does_not_touch_sources(tmp_path):
    t = _track(tmp_path, "x.mp3", "Encara", bpm=129, key="F")
    before = t.src.read_bytes()
    packmod.build_pack([t], tmp_path / "out" / "p", _meta())
    assert t.src.exists() and t.src.read_bytes() == before  # source untouched (copy, not move)


def test_build_pack_dedupes_collisions(tmp_path):
    tracks = [_track(tmp_path, "a.mp3", "Same", bpm=140, key="Fm"),
              _track(tmp_path, "b.mp3", "Same", bpm=140, key="Fm")]
    names = packmod.build_pack(tracks, tmp_path / "out" / "p", _meta())
    assert len(set(names)) == 2  # no overwrite — second is uniquely named


def test_index_html_escapes_titles(tmp_path):
    t = _track(tmp_path, "x.mp3", "<script>alert(1)</script>", bpm=120, key="C")
    packmod.build_pack([t], tmp_path / "out" / "p", _meta())
    idx = (tmp_path / "out" / "p" / "index.html").read_text()
    # the title is injected as JSON in a <script>; < > & are escaped so it can't break out
    assert "<script>alert(1)</script>" not in idx
    assert "\\u003cscript\\u003e" in idx


def test_index_html_is_a_turntable_player(tmp_path):
    t = _track(tmp_path, "x.mp3", "Beat One", bpm=140, key="Fm", genre="trap")
    packmod.build_pack([t], tmp_path / "out" / "p", _meta())
    idx = (tmp_path / "out" / "p" / "index.html").read_text()
    assert 'class="vinyl"' in idx and "@keyframes spin" in idx  # the spinning record
    assert 'id="viz"' in idx and "createAnalyser" in idx        # the audio-reactive visualizer
    assert '"f": "01 - Beat One [140 Fm].mp3"' in idx           # the track wired into the player
