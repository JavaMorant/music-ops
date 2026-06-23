from librarian import setcard


def test_split_label():
    assert setcard.split_label("Drake - One Dance") == ("Drake", "One Dance")
    assert setcard.split_label("One Dance") == ("", "One Dance")
    assert setcard.split_label("") == ("", "")
    # only splits on the first ' - '
    assert setcard.split_label("MK - 17 - Edit") == ("MK", "17 - Edit")


def test_streaming_links_encode_all_three():
    links = setcard.streaming_links("Drake - One Dance")
    assert links["Spotify"] == "https://open.spotify.com/search/Drake%20One%20Dance"
    assert links["SoundCloud"] == "https://soundcloud.com/search?q=Drake%20One%20Dance"
    assert links["Bandcamp"] == "https://bandcamp.com/search?q=Drake%20One%20Dance"


def test_streaming_links_encode_specials():
    # '&' and '/' must be percent-encoded so the URL stays valid
    links = setcard.streaming_links("Salt & Pepa - Push It / Remix")
    assert "%26" in links["Spotify"] and "%2F" in links["Spotify"]
    assert " " not in links["Bandcamp"]


def test_render_card_is_standalone_with_links_and_tracks():
    detail = {
        "fmt": "DL+", "n": 2, "auto_name": "DL+ set #3",
        "tracks": [
            {"label": "Drake - One Dance", "bpm": 104, "genre": "Afrobeats"},
            {"label": "Burna Boy - Ye", "bpm": 118, "genre": "Afrobeats"},
        ],
    }
    doc = setcard.render_card(detail, "Friday Night", stick="DIBSSS")
    assert doc.startswith("<!DOCTYPE html>")
    assert "Friday Night" in doc
    assert "One Dance" in doc and "Burna Boy" in doc
    assert "open.spotify.com" in doc and "soundcloud.com" in doc and "bandcamp.com" in doc
    assert "Afrobeats" in doc            # genre chip
    assert "energy arc" in doc           # bpm sparkline present (2+ bpms)
    assert "<style>" in doc and "</style>" in doc  # self-contained, no external css


def test_render_card_escapes_html():
    detail = {"fmt": "DL", "n": 1, "auto_name": "s",
              "tracks": [{"label": "AC/DC - Back <in> Black", "bpm": 0, "genre": ""}]}
    doc = setcard.render_card(detail, "<script>x</script>", stick="USB")
    assert "<script>x</script>" not in doc
    assert "&lt;script&gt;" in doc


def test_render_card_recording_only_links_urls():
    detail = {"fmt": "DL", "n": 1, "auto_name": "s",
              "tracks": [{"label": "A - B", "bpm": 120, "genre": "House"}]}
    # a local path is not a public link -> no recording button
    assert "Listen to the recording" not in setcard.render_card(detail, "s", recording="/Users/me/set.wav")
    # a URL is
    assert "Listen to the recording" in setcard.render_card(detail, "s", recording="https://soundcloud.com/x/y")
