"""Version detection: distinct versions are kept apart, alt-sources collapse."""

from __future__ import annotations

from librarian import versions as V


def test_distinct_versions_detected():
    assert V.is_distinct_version("Promiscuous (Intro)")
    assert V.is_distinct_version("Wannabe (Extended Mix)")
    assert V.is_distinct_version("One Dance (Acapella)")
    assert V.version_label("Title (VIP)") == "vip"


def test_plain_and_video_are_same_recording():
    assert not V.is_distinct_version("Promiscuous")
    assert V.is_alt_source("Promiscuous (Official Music Video) ft. Timbaland")
    assert V.is_alt_source("Wannabe (Lyrics)")
    # a video rip is NOT a distinct version
    assert not V.is_distinct_version("Wannabe (Official Video)")


def test_base_title_collapses_source_not_version():
    # same song, different source -> same base title
    assert V.base_title("Promiscuous") == \
        V.base_title("Promiscuous (Official Music Video) ft. Timbaland")
    # an intro edit shares the base song but is flagged distinct by version_label
    assert V.base_title("Promiscuous (Intro)") == V.base_title("Promiscuous")
    assert V.is_distinct_version("Promiscuous (Intro)")


def test_extended_vs_radio_share_base_but_are_distinct():
    assert V.base_title("Titanium (Extended Mix)") == V.base_title("Titanium (Radio Edit)")
    assert V.version_label("Titanium (Extended Mix)") == "extended"
    assert V.version_label("Titanium (Radio Edit)") == "radio edit"
