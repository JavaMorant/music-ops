# tests/test_camelot.py
from __future__ import annotations
from librarian import camelot as C


def test_parse_camelot_openkey_musical():
    assert C.parse_key("8A") == (8, "A")
    assert C.parse_key("12b") == (12, "B")
    assert C.parse_key("Am") == (8, "A")           # A minor
    assert C.parse_key("C") == (8, "B")            # C major
    assert C.parse_key("F#m") == (11, "A")
    assert C.parse_key("Ebmaj") == (5, "B")
    assert C.parse_key("1m") == (8, "A")           # Open Key -> Camelot (+7 offset)
    assert C.parse_key("1d") == (8, "B")
    assert C.parse_key(None) is None
    assert C.parse_key("garbage") is None


def test_harmonic_table():
    assert C.harmonic((8, "A"), (8, "A")) == 1.0            # same key
    assert C.harmonic((8, "A"), (9, "A")) == 0.90           # +1 wheel
    assert C.harmonic((8, "A"), (8, "B")) == 0.85           # relative maj/min
    assert C.harmonic((8, "A"), (10, "A"), rising=True) == 0.55   # +2 energy, rising
    assert C.harmonic((8, "A"), (10, "A"), rising=False) == 0.10  # +2 not allowed flat
    assert C.harmonic((8, "A"), (9, "B"), mode="loose") == 0.40   # diagonal, loose
    assert C.harmonic((8, "A"), (2, "A")) == 0.10           # far
    assert C.harmonic(None, (8, "A")) == 0.5                # unknown -> neutral
    assert C.harmonic((8, "A"), None) == 0.5


def test_harmonic_mode_off_is_neutral():
    # "off" disables the component entirely — same key and clashing key alike.
    assert C.harmonic((8, "A"), (8, "A"), mode="off") == 0.5
    assert C.harmonic((8, "A"), (2, "A"), mode="off") == 0.5
