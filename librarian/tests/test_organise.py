"""Pulse USB organise: play-history crate building + importable-playlist export."""

from __future__ import annotations

from pathlib import Path

from librarian import pulse_organise, pulse_usb


def test_crates_from_orders_by_play_count():
    sessions = [
        ["Drake - One Dance", "Burna Boy - Ye", "Wizkid - Essence"],
        ["Drake - One Dance", "Wizkid - Essence", "Burna Boy - Last Last"],
    ]
    meta = {pulse_usb._norm("Drake - One Dance"): {"path": "/Volumes/U/one.mp3"}}
    crates = pulse_usb._crates_from(sessions, meta, last_n=50, top=10)

    names = [c["name"] for c in crates]
    assert any("Most Played" in n for n in names)
    assert any("Crowd-Pleasers" in n for n in names)

    most = next(c for c in crates if "Most Played" in c["name"])
    # Drake - One Dance is the only track played in both sets -> first
    assert most["tracks"][0]["label"] == "Drake - One Dance"
    assert most["tracks"][0]["path"] == "/Volumes/U/one.mp3"   # path carried through
    # a track without a path still appears, just with an empty path
    ye = next(t for t in most["tracks"] if t["label"] == "Burna Boy - Ye")
    assert ye["path"] == ""


def test_crates_from_empty_is_empty():
    assert pulse_usb._crates_from([], {}) == []


def test_write_crates_emits_m3u8_and_readme(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pulse_organise, "DIR", tmp_path / "usb-crates")
    crates = [
        {"name": "⭐ Most Played", "tracks": [
            {"label": "A - B", "path": "/x/a.mp3"},
            {"label": "C - D", "path": ""},
        ]},
        {"name": "🔥 Hot Right Now", "tracks": []},   # empty -> skipped
    ]
    res = pulse_organise.write_crates("DIBSSS", crates)

    folder = Path(res["folder"])
    assert folder.is_dir() and folder.name == "DIBSSS"
    m3u = next(folder.glob("*.m3u8")).read_text()
    assert m3u.startswith("#EXTM3U")
    assert "#EXTINF:-1,A - B" in m3u and "/x/a.mp3" in m3u   # path written
    assert "C - D" in m3u                                     # label still listed
    assert (folder / "README.txt").exists()

    assert len(res["crates"]) == 1                            # empty crate skipped
    assert res["crates"][0]["tracks"] == 2 and res["crates"][0]["with_path"] == 1
    assert res["needs_paths"] is True                         # one track lacked a path


def test_write_crates_sanitises_names(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pulse_organise, "DIR", tmp_path / "usb-crates")
    res = pulse_organise.write_crates("DIB/SS:S*", [
        {"name": "▶ Openers", "tracks": [{"label": "X - Y", "path": ""}]}])
    # no path separators leaked into the on-disk folder/file names
    folder = Path(res["folder"])
    assert "/" not in folder.name and ":" not in folder.name
    assert list(folder.glob("*.m3u8"))
