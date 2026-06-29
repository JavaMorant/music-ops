"""Quarantine recovery: distinct versions flagged to recover, rips flagged dupe."""

from __future__ import annotations

from librarian import quarantine as Q


def test_classify_recover_vs_dupe_vs_plain():
    assert Q._classify("Promiscuous (Intro)")[2] == "recover"
    assert Q._classify("Wannabe (Extended Mix)")[2] == "recover"
    assert Q._classify("Promiscuous (Official Music Video) ft. Timbaland")[2] == "dupe"
    assert Q._classify("Wannabe (Lyrics)")[2] == "dupe"
    assert Q._classify("Promiscuous")[2] == "plain"


def test_scan_and_group(tmp_path):
    for name in [
        "Nelly Furtado - Promiscuous.mp3",
        "Nelly Furtado - Promiscuous (Intro).mp3",
        "Nelly Furtado - Promiscuous (Official Music Video) ft. Timbaland.mp3",
        "Spice Girls - Wannabe (Extended Mix).mp3",
    ]:
        (tmp_path / name).write_bytes(b"\x00")  # not real audio; reader falls back to filename

    items = Q.scan(tmp_path)
    assert len(items) == 4
    groups = Q.group_by_song(items)
    promiscuous = next(b for b in groups if "promiscuous" in b)
    assert len(groups[promiscuous]) == 3  # plain + intro + video share one base song

    recover = {it.title for it in items if it.kind == "recover"}
    assert any("Intro" in t for t in recover)
    assert any("Extended" in t for t in recover)
    assert Q.restore_list(tmp_path)  # non-empty
