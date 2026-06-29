"""Dedup v2: recording-level collapse, version preservation, never-delete."""

from __future__ import annotations

from pathlib import Path

from librarian.dedup_v2 import FileInfo, dedup_key, plan_groups, summarise


def fi(name, **kw):
    return FileInfo(path=Path(f"/lib/{name}.mp3"),
                    artist=kw.get("artist", "X"), title=kw.get("title", name),
                    mbid=kw.get("mbid"), bitrate=kw.get("bitrate", 320),
                    size=kw.get("size", 1000), rating=kw.get("rating", 0))


def test_alt_sources_of_same_recording_collapse():
    plain = fi("Promiscuous", artist="Nelly Furtado", title="Promiscuous")
    video = fi("Promiscuous (Official Music Video) ft. Timbaland",
               artist="Nelly Furtado", title="Promiscuous (Official Music Video)")
    lyrics = fi("Promiscuous (Lyrics)", artist="Nelly Furtado", title="Promiscuous (Lyrics)")
    groups = plan_groups([plain, video, lyrics])
    assert len(groups) == 1
    assert len(groups[0].drops) == 2  # 3 sources of one recording -> 1 keeper


def test_distinct_versions_are_kept_apart():
    plain = fi("Promiscuous", artist="Nelly Furtado", title="Promiscuous")
    intro = fi("Promiscuous (Intro)", artist="Nelly Furtado", title="Promiscuous (Intro)")
    extended = fi("Promiscuous (Extended Mix)", artist="Nelly Furtado",
                  title="Promiscuous (Extended Mix)")
    groups = plan_groups([plain, intro, extended])
    assert len(groups) == 3  # plain, intro, extended each survive
    assert all(len(g.drops) == 0 for g in groups)


def test_same_mbid_collapses_but_version_token_splits():
    a = fi("a", mbid="REC1", title="Song")
    b = fi("b", mbid="REC1", title="Song (Official Video)")  # same recording
    c = fi("c", mbid="REC1", title="Song (Intro)")           # edit -> keep apart
    groups = plan_groups([a, b, c])
    keys = {g.key for g in groups}
    assert ("mbid", "REC1", "") in keys
    assert ("mbid", "REC1", "intro") in keys
    assert len(groups) == 2


def test_keep_selection_prefers_rated_then_quality():
    low = fi("low", title="Song", bitrate=128, rating=0)
    rated = fi("rated", title="Song", bitrate=128, rating=5)
    hibitrate = fi("hi", title="Song", bitrate=320, rating=0)
    groups = plan_groups([low, hibitrate, rated])
    assert len(groups) == 1
    assert groups[0].keep is rated  # rating wins (keeps the cued copy)


def test_drops_are_reported_never_implied_deleted():
    dups = [fi("Song", title="Song"), fi("Song2", title="Song")]
    groups = plan_groups(dups)
    s = summarise(groups)
    # every input is accounted for as keeper or drop; nothing vanishes
    assert s["unique_keepers"] + s["drops"] == 2
    assert s["drops"] == 1
