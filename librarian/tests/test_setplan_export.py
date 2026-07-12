# tests/test_setplan_export.py
from __future__ import annotations
from librarian.setplan.pool import Candidate, norm
from librarian.setplan.spec import GigSpec
from librarian.setplan.search import Slot, SetPlan
from librarian.setplan.export import to_m3u8, to_markdown


def _plan():
    c = Candidate(path="/lib/Mnike.mp3", artist="Tyler ICU", title="Mnike", genre="amapiano",
                  bpm=112.0, camelot=(8, "A"), length_s=200.0, norm_key=norm("Tyler ICU Mnike"),
                  low_bitrate=False, plays=3)
    slot = Slot(index=0, candidate=c, reason="⭐ anchor (must-play)", clock_min=0.0)
    return SetPlan(spec=GigSpec(minutes=30), slots=[slot])


def test_m3u8_lists_track_paths(tmp_path):
    out = tmp_path / "set.m3u8"
    to_m3u8(_plan(), out)
    text = out.read_text()
    assert text.startswith("#EXTM3U")
    assert "/lib/Mnike.mp3" in text
    assert "Tyler ICU - Mnike" in text


def test_markdown_has_reasons_and_badges(tmp_path):
    out = tmp_path / "set.md"
    to_markdown(_plan(), out)
    text = out.read_text()
    assert "Mnike" in text and "8A" in text and "112" in text
    assert "anchor" in text.lower()
