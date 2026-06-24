"""Deterministic Artist/Title cleaning — strip noise, keep the real content."""

from __future__ import annotations

from librarian import tagclean


def c(artist, title, stem=""):
    return tagclean.clean_fields(artist, title, stem)


def test_strip_official_video_and_split():
    assert c("", "Drake - One Dance (Official Video)") == ("Drake", "One Dance")


def test_baked_in_artist_when_artist_field_duplicates_it():
    assert c("Drake", "Drake - One Dance (Official Audio)") == ("Drake", "One Dance")


def test_preserves_version_markers():
    assert c("Dua Lipa", "Levitating (Dirty)") == ("Dua Lipa", "Levitating (Dirty)")
    assert c("", "MK - 17 (Remix)") == ("MK", "17 (Remix)")


def test_preserves_real_subtitle_but_drops_lyrics():
    assert c("Lil Nas X", "MONTERO (Call Me By Your Name) (Lyrics)") == \
        ("Lil Nas X", "MONTERO (Call Me By Your Name)")


def test_preserves_feat():
    assert c("", "Shenseea - Blessed (feat. Tyga)") == ("Shenseea", "Blessed (feat. Tyga)")


def test_strips_emoji():
    assert c("Alicia Keys", "Empire State of Mind 🎵🎼🎶") == ("Alicia Keys", "Empire State of Mind")


def test_collapses_redundant_wrap():
    assert c("Bicep", "Glue ( Glue )")[1] == "Glue"
    assert c("", "Last Last ( Last Last )") == ("", "Last Last")


def test_strips_copy_marker_and_kbps():
    assert c("", "KRS-One - Sound of da Police (320 kbps)") == ("KRS-One", "Sound of da Police")
    assert c("Cassie", "Long Way 2 Go (2)") == ("Cassie", "Long Way 2 Go")


def test_strips_uploader_handle():
    assert c("", "Lady Gaga - Alejandro - musiclover11") == ("Lady Gaga", "Alejandro")


def test_strips_trailing_bare_noise():
    assert c("", "Bobby Shmurda 9+10=21 Song OFFICIAL MUSIC VIDEO HD")[1] == "Bobby Shmurda 9+10=21 Song"


def test_strips_standalone_audio_bracket():
    assert c("Burna Boy", "Last Last [Audio]") == ("Burna Boy", "Last Last")


def test_strips_trailing_extension_in_title():
    assert c("ODF", "ODF - Golden Dub (Warm Edit) (320 kbps).mp3") == ("ODF", "Golden Dub (Warm Edit)")


def test_does_not_overwrite_a_set_artist_on_a_remix():
    # "Title - Remixer" file with the remixer already in the artist field: keep it
    a, t = c("Lexa Raballo", "Abba - Gimme! (Lexa Raballo Afro House Remix)")
    assert a == "Lexa Raballo"   # never flipped to "Abba"


def test_never_blanks_existing_artist():
    assert c("Bicep", "Glue") == ("Bicep", "Glue")


def test_clean_pair_is_unchanged():
    assert c("AJ Tracey", "Psych Out!") == ("AJ Tracey", "Psych Out!")
    assert c("Stray Kids", "MANIAC") == ("Stray Kids", "MANIAC")
