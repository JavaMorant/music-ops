"""sanitize_component must neutralise adversarial tag values — a garbage genre
or Artist/Title can never escape the library root or break a move."""

from __future__ import annotations

from pathlib import Path

import pytest

from librarian.paths import audio_files, sanitize_component


@pytest.mark.parametrize(
    "raw, banned",
    [
        ("../../etc/passwd", ("/", "..")),
        ("AC/DC", ("/",)),
        ("a:b\\c", (":", "\\")),
        ("drum\x00bass", ("\x00",)),
    ],
)
def test_no_separators_or_traversal_survive(raw, banned):
    out = sanitize_component(raw)
    for token in banned:
        if token == "..":
            assert ".." not in out or out.strip(".") != ""  # never a pure-dots component
        else:
            assert token not in out


def test_empty_and_all_illegal_fall_back_to_unknown():
    assert sanitize_component("") == "Unknown"
    assert sanitize_component("   ") == "Unknown"
    assert sanitize_component("...") == "Unknown"


def test_overlong_component_is_capped():
    out = sanitize_component("X" * 500)
    assert 0 < len(out) <= 200


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"audio")


def test_audio_files_finds_real_tracks_and_skips_state_dirs(tmp_path: Path):
    """C1 regression: run backups/journals stored under ``.librarian`` (and the
    quarantine dir) must never be scanned as if they were library tracks."""
    _touch(tmp_path / "US Rap" / "Track.mp3")
    _touch(tmp_path / "Garage" / "Other.m4a")
    # Run backup copies live inside a genre-named subfolder of .librarian/runs.
    _touch(tmp_path / ".librarian" / "runs" / "r1" / "backup" / "Afro House" / "Track.mp3")
    _touch(tmp_path / ".librarian" / "organise" / "identity.json.mp3")
    # Set-aside dirs and macOS AppleDouble sidecars are noise, not tracks.
    _touch(tmp_path / "_quarantine" / "Dropped.mp3")
    _touch(tmp_path / ".quarantine" / "Broken.mp3")
    _touch(tmp_path / "US Rap" / "._Track.mp3")

    found = {p.relative_to(tmp_path).as_posix() for p in audio_files(tmp_path)}

    assert found == {"US Rap/Track.mp3", "Garage/Other.m4a"}


def test_audio_files_extra_skip_dirs_still_honoured(tmp_path: Path):
    _touch(tmp_path / "House" / "Keep.mp3")
    _touch(tmp_path / "Inbox" / "Staged.mp3")

    found = {p.name for p in audio_files(tmp_path, extra_skip_dirs=frozenset({"Inbox"}))}

    assert found == {"Keep.mp3"}
