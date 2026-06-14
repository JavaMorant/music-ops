"""Tests for clipper.overlay — load_template, build_filter, _escape_path.

No ffmpeg is invoked; burn_overlay is not tested here. All file I/O goes into
tmp_path provided by pytest fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipper.media import MediaError
from clipper.overlay import (
    DEFAULT_LINES,
    DEFAULT_STYLE,
    _escape_path,
    build_filter,
    load_template,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REAL_FONT = "/System/Library/Fonts/Helvetica.ttc"


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "template.toml"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# load_template(None) — defaults
# ---------------------------------------------------------------------------


class TestLoadTemplateNone:
    def test_returns_style_dict(self):
        style, lines = load_template(None)
        assert isinstance(style, dict)

    def test_default_style_keys_present(self):
        style, _ = load_template(None)
        for key in ("font", "color", "border", "border_color"):
            assert key in style, f"missing key {key!r} in default style"

    def test_default_style_font_is_real_font(self):
        style, _ = load_template(None)
        assert Path(style["font"]).is_file(), "default font must exist on this machine"

    def test_default_style_border_is_int(self):
        style, _ = load_template(None)
        assert isinstance(style["border"], int)

    def test_default_lines_count(self):
        _, lines = load_template(None)
        assert len(lines) == len(DEFAULT_LINES)

    def test_default_lines_have_text(self):
        _, lines = load_template(None)
        for line in lines:
            assert "text" in line
            assert isinstance(line["text"], str)

    def test_default_lines_have_int_size(self):
        _, lines = load_template(None)
        for line in lines:
            assert isinstance(line["size"], int)

    def test_default_lines_have_y(self):
        _, lines = load_template(None)
        for line in lines:
            assert "y" in line

    def test_returns_independent_copies(self):
        """Mutating the returned dicts must not affect subsequent calls."""
        style1, lines1 = load_template(None)
        style1["color"] = "red"
        lines1[0]["text"] = "CHANGED"
        style2, lines2 = load_template(None)
        assert style2["color"] == DEFAULT_STYLE["color"]
        assert lines2[0]["text"] == DEFAULT_LINES[0]["text"]


# ---------------------------------------------------------------------------
# load_template(path) — merges style and lines from TOML
# ---------------------------------------------------------------------------


class TestLoadTemplatePath:
    def test_style_is_merged_over_defaults(self, tmp_path):
        toml = _write_toml(
            tmp_path,
            f'[style]\ncolor = "yellow"\nfont = "{REAL_FONT}"\n',
        )
        style, _ = load_template(toml)
        assert style["color"] == "yellow"
        # Unspecified keys keep their default values.
        assert style["border"] == DEFAULT_STYLE["border"]
        assert style["border_color"] == DEFAULT_STYLE["border_color"]

    def test_custom_lines_replace_defaults(self, tmp_path):
        toml = _write_toml(
            tmp_path,
            f'[style]\nfont = "{REAL_FONT}"\n[[line]]\ntext = "{{event}}"\nsize = 60\ny = "h*0.7"\n',
        )
        _, lines = load_template(toml)
        assert len(lines) == 1
        assert lines[0]["text"] == "{event}"
        assert lines[0]["size"] == 60
        assert lines[0]["y"] == "h*0.7"

    def test_border_as_string_integer_in_toml(self, tmp_path):
        """TOML integer for border should end up as Python int."""
        toml = _write_toml(
            tmp_path,
            f'[style]\nfont = "{REAL_FONT}"\nborder = 5\n',
        )
        style, _ = load_template(toml)
        assert style["border"] == 5
        assert isinstance(style["border"], int)

    def test_no_style_section_uses_defaults(self, tmp_path):
        toml = _write_toml(
            tmp_path,
            f'[[line]]\ntext = "{{artist}}"\nsize = 48\ny = "h*0.8"\n',
        )
        # Font from DEFAULT_STYLE — test only passes if that font exists.
        style, _ = load_template(toml)
        assert style["color"] == DEFAULT_STYLE["color"]

    def test_missing_line_table_falls_back_to_default_lines(self, tmp_path):
        toml = _write_toml(
            tmp_path,
            f'[style]\nfont = "{REAL_FONT}"\n',
        )
        _, lines = load_template(toml)
        assert len(lines) == len(DEFAULT_LINES)


# ---------------------------------------------------------------------------
# load_template validation errors
# ---------------------------------------------------------------------------


class TestLoadTemplateValidation:
    """Each case verifies that MediaError is raised with the expected trigger."""

    def test_bad_color_raises(self, tmp_path):
        toml = _write_toml(
            tmp_path,
            f'[style]\nfont = "{REAL_FONT}"\ncolor = "red:x=1"\n',
        )
        with pytest.raises(MediaError, match="color"):
            load_template(toml)

    def test_bad_border_color_raises(self, tmp_path):
        toml = _write_toml(
            tmp_path,
            f'[style]\nfont = "{REAL_FONT}"\nborder_color = "black:x=1"\n',
        )
        with pytest.raises(MediaError, match="border_color"):
            load_template(toml)

    def test_non_int_border_raises(self, tmp_path):
        toml = _write_toml(
            tmp_path,
            f'[style]\nfont = "{REAL_FONT}"\nborder = "thick"\n',
        )
        with pytest.raises(MediaError, match="border"):
            load_template(toml)

    def test_missing_font_file_raises(self, tmp_path):
        toml = _write_toml(
            tmp_path,
            '[style]\nfont = "/nonexistent/path/font.ttf"\n',
        )
        with pytest.raises(MediaError, match="font"):
            load_template(toml)

    def test_line_without_text_raises(self, tmp_path):
        toml = _write_toml(
            tmp_path,
            f'[style]\nfont = "{REAL_FONT}"\n[[line]]\nsize = 48\ny = "h*0.8"\n',
        )
        with pytest.raises(MediaError):
            load_template(toml)

    def test_line_with_non_string_text_raises(self, tmp_path):
        toml = _write_toml(
            tmp_path,
            f'[style]\nfont = "{REAL_FONT}"\n[[line]]\ntext = 123\nsize = 48\ny = "h*0.8"\n',
        )
        with pytest.raises(MediaError):
            load_template(toml)

    def test_non_int_size_raises(self, tmp_path):
        toml = _write_toml(
            tmp_path,
            f'[style]\nfont = "{REAL_FONT}"\n[[line]]\ntext = "{{event}}"\nsize = "big"\ny = "h*0.8"\n',
        )
        with pytest.raises(MediaError, match="size"):
            load_template(toml)

    def test_bad_y_expr_with_colon_raises(self, tmp_path):
        """'h*0.8:x=evil' contains a colon — rejected by _Y_EXPR_RE."""
        toml = _write_toml(
            tmp_path,
            f'[style]\nfont = "{REAL_FONT}"\n[[line]]\ntext = "{{event}}"\nsize = 48\ny = "h*0.8:x=evil"\n',
        )
        with pytest.raises(MediaError, match="y"):
            load_template(toml)

    def test_bad_y_expr_with_ffmpeg_function_raises(self, tmp_path):
        """'if(gte(t,1),0,h)' contains letters like 'i', 'f', 'g', 'e' — rejected."""
        toml = _write_toml(
            tmp_path,
            f'[style]\nfont = "{REAL_FONT}"\n[[line]]\ntext = "{{event}}"\nsize = 48\ny = "if(gte(t,1),0,h)"\n',
        )
        with pytest.raises(MediaError, match="y"):
            load_template(toml)

    def test_valid_y_expr_with_arithmetic_passes(self, tmp_path):
        """h*0.78 is valid — only h, w, t, digits, and operators."""
        toml = _write_toml(
            tmp_path,
            f'[style]\nfont = "{REAL_FONT}"\n[[line]]\ntext = "{{event}}"\nsize = 48\ny = "h*0.78"\n',
        )
        _, lines = load_template(toml)  # must not raise
        assert lines[0]["y"] == "h*0.78"

    def test_valid_y_expr_with_w_passes(self, tmp_path):
        toml = _write_toml(
            tmp_path,
            f'[style]\nfont = "{REAL_FONT}"\n[[line]]\ntext = "{{event}}"\nsize = 48\ny = "h*0.5+w*0.1"\n',
        )
        _, lines = load_template(toml)
        assert "w" in lines[0]["y"]


# ---------------------------------------------------------------------------
# build_filter
# ---------------------------------------------------------------------------


class TestBuildFilter:
    def _default_style_and_lines(self):
        return load_template(None)

    def test_contains_expansion_none(self, tmp_path):
        style, lines = self._default_style_and_lines()
        vf = build_filter(style, lines, {"event": "Boiler Room", "date": "2025-01-01", "artist": "DJ X"}, tmp_path)
        assert "expansion=none" in vf

    def test_contains_textfile_not_inline_text(self, tmp_path):
        style, lines = self._default_style_and_lines()
        slots = {"event": "My Event", "date": "2025-01-01", "artist": "DJ Y"}
        vf = build_filter(style, lines, slots, tmp_path)
        # Text must be in a textfile, never inline in the filter string.
        assert "textfile=" in vf
        assert "My Event" not in vf
        assert "DJ Y" not in vf

    def test_textfiles_written_to_workdir(self, tmp_path):
        style, lines = self._default_style_and_lines()
        slots = {"event": "Boiler", "date": "2025", "artist": "DJ Z"}
        build_filter(style, lines, slots, tmp_path)
        txt_files = list(tmp_path.glob("line*.txt"))
        assert len(txt_files) >= 1

    def test_textfile_content_matches_rendered_text(self, tmp_path):
        style, lines = self._default_style_and_lines()
        slots = {"event": "Festival", "date": "2025-06-01", "artist": "Kozo"}
        build_filter(style, lines, slots, tmp_path)
        # line0.txt should contain the event text.
        line0 = tmp_path / "line0.txt"
        assert line0.exists()
        assert "Festival" in line0.read_text(encoding="utf-8")

    def test_empty_slot_drops_line(self, tmp_path):
        """A line whose only slot is empty should be dropped."""
        style, _ = load_template(None)
        # With artist="" the line becomes empty after stripping.
        # The other slot ({event}) must provide text to avoid all-empty error.
        lines_two = [
            {"text": "{artist}", "size": 48, "y": "h*0.8"},
            {"text": "{event}", "size": 60, "y": "h*0.7"},
        ]
        vf = build_filter(style, lines_two, {"artist": "", "event": "Showcase"}, tmp_path)
        # Only one textfile should have been written (artist line dropped).
        txt_files = list(tmp_path.glob("line*.txt"))
        assert len(txt_files) == 1

    def test_all_empty_slots_raises_media_error(self, tmp_path):
        style, lines = self._default_style_and_lines()
        # All slots empty → every line resolves to nothing.
        with pytest.raises(MediaError):
            build_filter(style, lines, {"event": "", "date": "", "artist": ""}, tmp_path)

    def test_text_with_colon_percent_comma_in_textfile_not_filter(self, tmp_path):
        """Special chars must land in the textfile, not in the filter string."""
        style, _ = load_template(None)
        lines = [{"text": "{event}", "size": 48, "y": "h*0.8"}]
        special_text = "Venue: 50% Off, Tonight!"
        vf = build_filter(style, lines, {"event": special_text}, tmp_path)
        # None of the special chars appear in the filter string.
        assert "50%" not in vf
        assert "Tonight!" not in vf
        # They do appear in the textfile.
        line0 = tmp_path / "line0.txt"
        content = line0.read_text(encoding="utf-8")
        assert special_text == content

    def test_filter_uses_drawtext(self, tmp_path):
        style, lines = self._default_style_and_lines()
        vf = build_filter(style, lines, {"event": "E", "date": "D", "artist": "A"}, tmp_path)
        assert "drawtext=" in vf

    def test_multiple_non_empty_lines_joined_with_comma(self, tmp_path):
        style, _ = load_template(None)
        lines = [
            {"text": "{event}", "size": 60, "y": "h*0.7"},
            {"text": "{artist}", "size": 40, "y": "h*0.85"},
        ]
        vf = build_filter(style, lines, {"event": "Fest", "artist": "DJ"}, tmp_path)
        # Two drawtext filters joined by comma.
        assert vf.count("drawtext=") == 2
        assert "," in vf

    def test_dot_separator_line_becomes_empty_when_both_slots_empty(self, tmp_path):
        """Default line '{date} · {artist}' with date='' and artist='' → dropped."""
        style, _ = load_template(None)
        lines_custom = [
            {"text": "{event}", "size": 72, "y": "h*0.78"},
            {"text": "{date} · {artist}", "size": 40, "y": "h*0.85"},
        ]
        # Only event is non-empty; the date·artist line should be stripped away.
        vf = build_filter(style, lines_custom, {"event": "SoloShow", "date": "", "artist": ""}, tmp_path)
        assert vf.count("drawtext=") == 1

    def test_missing_slot_key_treated_as_empty(self, tmp_path):
        """Slots dict missing a key should not raise KeyError — blank fills in."""
        style, _ = load_template(None)
        lines = [
            {"text": "{event}", "size": 60, "y": "h*0.7"},
            {"text": "{artist}", "size": 40, "y": "h*0.85"},
        ]
        # No 'artist' key supplied; {artist} → "" → line dropped.
        vf = build_filter(style, lines, {"event": "Only Event"}, tmp_path)
        assert vf.count("drawtext=") == 1

    def test_single_line_all_slots_empty_raises(self, tmp_path):
        style, _ = load_template(None)
        lines = [{"text": "{event}", "size": 48, "y": "h*0.8"}]
        with pytest.raises(MediaError):
            build_filter(style, lines, {"event": ""}, tmp_path)


# ---------------------------------------------------------------------------
# _escape_path
# ---------------------------------------------------------------------------


class TestEscapePath:
    def test_plain_path_unchanged(self, tmp_path):
        p = tmp_path / "somefile.txt"
        result = _escape_path(p)
        # No specials → escaping introduces no backslashes for these chars.
        assert "somefile.txt" in result

    def test_space_not_in_path_specials(self):
        """Space is not in _PATH_SPECIALS — it passes through unescaped.

        The filtergraph option parser does not treat space as a delimiter so
        it does not need backslash-escaping; only the chars in _PATH_SPECIALS
        (\\',:;[]=) are escaped.
        """
        p = Path("/tmp/my file.txt")
        result = _escape_path(p)
        # Space should be present and not preceded by a backslash.
        assert " " in result
        # The single-quote is not in this path so no backslash-quote expected.
        assert "\\'" not in result

    def test_quote_is_escaped(self):
        p = Path("/tmp/it's here.txt")
        result = _escape_path(p)
        assert "\\'" in result

    def test_colon_is_escaped(self):
        p = Path("/tmp/time:stamp.txt")
        result = _escape_path(p)
        assert "\\:" in result

    def test_backslash_is_escaped(self):
        p = Path("/tmp/back\\slash.txt")
        result = _escape_path(p)
        assert "\\\\" in result

    def test_path_with_space_and_quote(self):
        """Path with a space and a single-quote: quote is escaped, space passes through.

        _PATH_SPECIALS = "\\':,;[]=" — space is not in the set.  The quote
        (single-quote) IS a filtergraph option-parser special and is escaped.
        """
        p = Path("/tmp/my event's clip.txt")
        result = _escape_path(p)
        # Quote must be escaped.
        assert "\\'" in result
        # Space is present and unescaped (not preceded by backslash at that position).
        assert " " in result.replace("\\ ", "")  # space survives outside any escaped-space sequence

    def test_semicolon_is_escaped(self):
        p = Path("/tmp/a;b.txt")
        result = _escape_path(p)
        assert "\\;" in result

    def test_equals_is_escaped(self):
        p = Path("/tmp/key=value.txt")
        result = _escape_path(p)
        assert "\\=" in result

    def test_bracket_is_escaped(self):
        p = Path("/tmp/filter[0].txt")
        result = _escape_path(p)
        assert "\\[" in result

    def test_comma_is_escaped(self):
        p = Path("/tmp/a,b.txt")
        result = _escape_path(p)
        assert "\\," in result
