"""sanitize_component must neutralise adversarial tag values — a garbage genre
or Artist/Title can never escape the library root or break a move."""

from __future__ import annotations

import pytest

from librarian.paths import sanitize_component


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
