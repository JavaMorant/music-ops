from releases.infer import guess_genre, infer_stage, parse_bpm, parse_key


class TestStage:
    def test_taxonomy_folders_map_to_stages(self):
        assert infer_stage(["Beats", "Tracks", "Complete Tracks", "Song", "X"]) == "complete"
        assert infer_stage(["Beats", "Tracks", "Need Arranged : Deep Progress", "X"]) == "need-arranged"
        assert infer_stage(["Beats", "Tracks", "Remixes In Progress", "X"]) == "remix"
        assert infer_stage(["Beats", "Tracks", "Return to", "Potential", "X"]) == "return-to"
        assert infer_stage(["Beats", "Tracks", "Mels", "X"]) == "mels"
        assert infer_stage(["Beats", "Tracks", "Project bones"]) == "bones"
        assert infer_stage(["Beats", "Tracks", "Track List"]) == "track-list"

    def test_unknown_path_is_uncategorized(self):
        assert infer_stage(["Beats", "ooooo"]) == "uncategorized"
        assert infer_stage([]) == "uncategorized"


class TestBpm:
    def test_parses_plain_and_suffixed(self):
        assert parse_bpm("Revive me 164.wav") == 164
        assert parse_bpm("octane - 91bpm - x") == 91
        assert parse_bpm("Encara_129_F_Maj_Dibs") == 129

    def test_suffixed_wins_over_plain(self):
        assert parse_bpm("track 200 at 128bpm") == 128

    def test_ignores_non_tempo_numbers(self):
        assert parse_bpm("2 manny") is None       # leading token, not standalone tempo
        assert parse_bpm("808 vibes") is None      # out of range
        assert parse_bpm("20202026") is None       # year-ish blob
        assert parse_bpm("just a vibe") is None


class TestKey:
    def test_attached_forms(self):
        assert parse_key("joonya 137 Cmin") == "Cm"
        assert parse_key("thing C#m") == "C#m"
        assert parse_key("x Dminor") == "Dm"

    def test_split_form(self):
        assert parse_key("Encara_129_F_Maj_Dibs") == "F"
        assert parse_key("tune F# min here") == "F#m"

    def test_bare_letter_is_ignored(self):
        assert parse_key("A Tale of Two") is None
        assert parse_key("nothing here") is None


class TestGenre:
    def test_keyword_hits(self):
        assert guess_genre("amapiano type beat") == "amapiano"
        assert guess_genre("Sweet Gqom to the Max") == "gqom"
        assert guess_genre("uk drill kit vaccine") == "drill"
        assert guess_genre("Drake jersey club remix") == "jersey club"

    def test_default_unknown(self):
        assert guess_genre("just a title") == "unknown"

    def test_no_substring_false_positives(self):
        # whole-word matching only — these must NOT mistag
        assert guess_genre("scrap idea") == "unknown"        # not rap
        assert guess_genre("warehouse vibes") == "unknown"   # not house
        assert guess_genre("drilling deep") == "unknown"     # not drill
        assert guess_genre("extrapolate") == "unknown"       # not trap
        # but real whole-word hits still work
        assert guess_genre("amapiano type beat") == "amapiano"
        assert guess_genre("piano house thing") == "amapiano"
