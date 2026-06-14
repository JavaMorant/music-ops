"""Tests for pure helpers in analyze and manifest modules.

Covers:
- select_segments exact-abutment boundary (spacing gap allowed vs rejected)
- _resample_to_seconds frame→second mapping including the tail second
- format_timestamp
- write_manifest / write_captions_stub (tmp_path, unicode + space in source path)
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from clipper.analyze import Segment, _resample_to_seconds, select_segments
from clipper.manifest import format_timestamp, write_captions_stub, write_manifest


# ---------------------------------------------------------------------------
# select_segments – exact-abutment boundary
# ---------------------------------------------------------------------------


def _abutment_scores(peak_a_start: int, peak_b_start: int, clip_len: int) -> np.ndarray:
    """Return a scores array with two non-overlapping peaks of value 1.0 and 0.9.

    Peak widths equal clip_len so each window formed by the convolution is
    entirely within one peak region, giving deterministic window scores.
    """
    size = max(peak_a_start + clip_len, peak_b_start + clip_len) + clip_len
    s = np.zeros(size)
    s[peak_a_start : peak_a_start + clip_len] = 1.0
    s[peak_b_start : peak_b_start + clip_len] = 0.9
    return s


class TestSelectSegmentsAbutmentBoundary:
    """Gap of exactly `spacing` must be accepted; gap of spacing-1 must be rejected.

    All tests in this class pass lead_in=0 and drop_weight=0.0 to isolate the
    spacing predicate from the drop-anchoring and rise-reward features — those
    are covered separately in test_select_segments.py.  With lead_in=0 the
    candidate start positions are not shifted earlier, and with drop_weight=0.0
    the blended score is purely the sustained-level signal so peak ordering is
    determined only by amplitude (peak A at 1.0 always beats peak B at 0.9).
    """

    CLIP_LEN = 10
    SPACING = 5

    def test_exact_abutment_is_accepted(self):
        # Chosen window: [0, 10).  Candidate at start=15: gap = 15-10 = 5 = spacing.
        # Condition: start >= c.end + spacing  →  15 >= 15  → True  → accepted.
        scores = _abutment_scores(
            peak_a_start=0, peak_b_start=self.CLIP_LEN + self.SPACING, clip_len=self.CLIP_LEN
        )
        segs = select_segments(
            scores,
            clip_len=self.CLIP_LEN,
            max_clips=2,
            spacing=self.SPACING,
            lead_in=0,
            drop_weight=0.0,
        )
        starts = sorted(s.start for s in segs)
        assert len(segs) == 2, "both windows should be accepted"
        assert starts[0] == 0.0
        assert starts[1] == float(self.CLIP_LEN + self.SPACING), (
            f"window at start={self.CLIP_LEN + self.SPACING} should be accepted "
            f"(gap exactly equals spacing={self.SPACING})"
        )

    def test_one_short_of_spacing_is_rejected(self):
        # peak A at 0 (value 1.0) chosen first; peak B at 14 (value 0.9) is the
        # next candidate.  gap = 14 - (0 + 10) = 4 < spacing=5 → rejected.
        # Condition: 14 >= 15 → False; 14+10+5=29 <= 0 → False → rejected.
        # lead_in=0, drop_weight=0.0 ensure peak ordering follows amplitude only,
        # so peak A at start=0 is always chosen before peak B at start=14.
        scores = _abutment_scores(
            peak_a_start=0,
            peak_b_start=self.CLIP_LEN + self.SPACING - 1,  # start=14
            clip_len=self.CLIP_LEN,
        )
        segs = select_segments(
            scores,
            clip_len=self.CLIP_LEN,
            max_clips=2,
            spacing=self.SPACING,
            lead_in=0,
            drop_weight=0.0,
        )
        starts = [s.start for s in segs]
        assert float(self.CLIP_LEN + self.SPACING - 1) not in starts, (
            f"window at start={self.CLIP_LEN + self.SPACING - 1} should be rejected "
            f"(gap={self.SPACING - 1} < spacing={self.SPACING})"
        )

    def test_symmetry_new_window_before_chosen(self):
        # The symmetric direction: a high-score window chosen at a later position,
        # and a candidate that starts before it.  For chosen c.start=20, c.end=30:
        # candidate at start=5 satisfies start + clip_len + spacing <= c.start
        #   → 5 + 10 + 5 = 20 <= 20 → True → accepted.
        # lead_in=0, drop_weight=0.0 keep positions at the amplitude peaks (20 and 5).
        n = 60
        scores = np.zeros(n)
        scores[20:30] = 1.0   # dominant peak -> chosen first at start=20
        scores[5:15] = 0.9    # candidate at start=5: 5+10+5=20 <= 20 → accepted
        segs = select_segments(
            scores,
            clip_len=self.CLIP_LEN,
            max_clips=2,
            spacing=self.SPACING,
            lead_in=0,
            drop_weight=0.0,
        )
        starts = sorted(s.start for s in segs)
        assert len(segs) == 2
        assert starts[0] == 5.0
        assert starts[1] == 20.0

    def test_symmetric_one_short_rejected(self):
        # candidate at start=6: 6+10+5=21 <= 20 → False; 6 >= 30+5=35 → False → rejected.
        # lead_in=0, drop_weight=0.0 keep peak at start=20 as the top candidate so
        # it is chosen first, then the candidate at 6 is tested and rejected.
        n = 60
        scores = np.zeros(n)
        scores[20:30] = 1.0
        scores[6:16] = 0.9   # gap from end=16 to c.start=20 is 4 < spacing=5 → reject
        segs = select_segments(
            scores,
            clip_len=self.CLIP_LEN,
            max_clips=2,
            spacing=self.SPACING,
            lead_in=0,
            drop_weight=0.0,
        )
        starts = [s.start for s in segs]
        assert 6.0 not in starts, "candidate with gap=4 < spacing=5 must be rejected"


# ---------------------------------------------------------------------------
# _resample_to_seconds
# ---------------------------------------------------------------------------


class TestResampleToSeconds:
    def test_exact_fps_maps_frames_to_seconds(self):
        # fps=2, duration=3: 3 output seconds, each covers exactly 2 frames.
        frames = np.array([0.2, 0.4, 0.6, 0.8, 1.0, 1.2])
        result = _resample_to_seconds(frames, frames_per_second=2.0, duration=3.0)
        assert result.shape == (3,)
        assert result[0] == pytest.approx(0.3)   # mean([0.2, 0.4])
        assert result[1] == pytest.approx(0.7)   # mean([0.6, 0.8])
        assert result[2] == pytest.approx(1.1)   # mean([1.0, 1.2])

    def test_tail_second_returns_zero_when_frames_exhausted(self):
        # Only 4 frames provided but duration says 3 seconds (6 frames expected).
        # Second 2 (s=2): a = int(2*2)=4, which equals len(frames)=4 → yields 0.0.
        frames = np.array([0.5, 0.5, 0.5, 0.5])
        result = _resample_to_seconds(frames, frames_per_second=2.0, duration=3.0)
        assert result.shape == (3,)
        assert result[0] == pytest.approx(0.5)
        assert result[1] == pytest.approx(0.5)
        assert result[2] == pytest.approx(0.0), "tail second with no frames must be 0.0"

    def test_sub_second_duration_produces_one_element(self):
        # duration=0.5 → max(1, int(0.5))=1 → single output second.
        frames = np.array([0.7, 0.3])
        result = _resample_to_seconds(frames, frames_per_second=4.0, duration=0.5)
        assert result.shape == (1,)
        # s=0: a=0, b=max(1, int(4.0))=4, frames[0:4]=[0.7, 0.3] → mean=0.5
        assert result[0] == pytest.approx(0.5)

    def test_non_integer_fps_all_ones(self):
        # Simulate a real librosa fps (22050/512 ≈ 43.07).
        fps = 22050 / 512
        duration = 5.0
        n_frames = int(fps * duration) + 10  # extra frames beyond duration
        frames = np.ones(n_frames)
        result = _resample_to_seconds(frames, frames_per_second=fps, duration=duration)
        assert result.shape == (5,)
        np.testing.assert_allclose(result, 1.0, err_msg="all-ones frames should map to 1.0")

    def test_monotone_increasing_frames_output_increases(self):
        # Each successive second should have a higher mean than the previous one.
        fps = 3.0
        duration = 4.0
        # frames: indices 0..11, value = index (increasing)
        frames = np.arange(12, dtype=float)
        result = _resample_to_seconds(frames, frames_per_second=fps, duration=duration)
        assert result.shape == (4,)
        for i in range(len(result) - 1):
            assert result[i] < result[i + 1], (
                f"monotone-increasing input: result[{i}]={result[i]} should be "
                f"< result[{i+1}]={result[i+1]}"
            )

    def test_single_frame_per_second(self):
        # fps=1.0, each second maps exactly one frame.
        frames = np.array([0.1, 0.5, 0.9])
        result = _resample_to_seconds(frames, frames_per_second=1.0, duration=3.0)
        assert result.shape == (3,)
        # s=0: a=0, b=max(1,1)=1, frames[0:1]=[0.1]
        # s=1: a=1, b=2, frames[1:2]=[0.5]
        # s=2: a=2, b=3, frames[2:3]=[0.9]
        np.testing.assert_allclose(result, [0.1, 0.5, 0.9])


# ---------------------------------------------------------------------------
# format_timestamp
# ---------------------------------------------------------------------------


class TestFormatTimestamp:
    def test_zero(self):
        assert format_timestamp(0) == "00:00:00"

    def test_sub_minute(self):
        assert format_timestamp(45) == "00:00:45"

    def test_exactly_one_minute(self):
        assert format_timestamp(60) == "00:01:00"

    def test_sub_hour(self):
        assert format_timestamp(3599) == "00:59:59"

    def test_exactly_one_hour(self):
        assert format_timestamp(3600) == "01:00:00"

    def test_over_one_hour(self):
        assert format_timestamp(3661) == "01:01:01"

    def test_float_is_truncated_not_rounded(self):
        # 45.9 seconds truncates to 45, not 46.
        assert format_timestamp(45.9) == "00:00:45"

    def test_large_value(self):
        # 2h 30m 15s = 9015s
        assert format_timestamp(9015) == "02:30:15"


# ---------------------------------------------------------------------------
# write_manifest
# ---------------------------------------------------------------------------


class TestWriteManifest:
    def _make_clips(self, out_dir: Path) -> list[tuple[Path, Segment]]:
        return [
            (out_dir / "clip_001.mp4", Segment(0.0, 30.0, 0.950)),
            (out_dir / "clip_002.mp4", Segment(90.0, 30.0, 0.720)),
        ]

    def test_creates_manifest_csv(self, tmp_path):
        clips = self._make_clips(tmp_path)
        result = write_manifest(tmp_path, Path("/some/source.wav"), clips)
        assert result == tmp_path / "manifest.csv"
        assert result.exists()

    def test_header_row(self, tmp_path):
        clips = self._make_clips(tmp_path)
        write_manifest(tmp_path, Path("/some/source.wav"), clips)
        with (tmp_path / "manifest.csv").open(newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert rows[0] == ["clip", "source", "source_timestamp", "duration_s", "score"]

    def test_data_rows_count(self, tmp_path):
        clips = self._make_clips(tmp_path)
        write_manifest(tmp_path, Path("/some/source.wav"), clips)
        with (tmp_path / "manifest.csv").open(newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert len(rows) == 3  # header + 2 data rows

    def test_data_row_fields(self, tmp_path):
        clips = self._make_clips(tmp_path)
        source = Path("/some/source.wav")
        write_manifest(tmp_path, source, clips)
        with (tmp_path / "manifest.csv").open(newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        # First data row: clip_001.mp4, source path, 00:00:00, 30, 0.950
        assert rows[1][0] == "clip_001.mp4"
        assert rows[1][1] == str(source)
        assert rows[1][2] == "00:00:00"
        assert rows[1][3] == "30"
        assert rows[1][4] == "0.950"
        # Second data row: clip_002.mp4, 00:01:30, 30, 0.720
        assert rows[2][0] == "clip_002.mp4"
        assert rows[2][2] == "00:01:30"
        assert rows[2][3] == "30"
        assert rows[2][4] == "0.720"

    def test_source_path_with_spaces_and_unicode(self, tmp_path):
        # Source path containing double spaces and a non-ASCII character (ö).
        source = Path("/sets/Afro House  Göteborg 2025.wav")
        clips = self._make_clips(tmp_path)
        write_manifest(tmp_path, source, clips)
        with (tmp_path / "manifest.csv").open(newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        # The source column must preserve the path exactly, including spaces and unicode.
        assert rows[1][1] == str(source)
        assert "Göteborg" in rows[1][1]
        assert "  " in rows[1][1]  # double space preserved

    def test_empty_clips_list(self, tmp_path):
        result = write_manifest(tmp_path, Path("/source.wav"), [])
        with result.open(newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert rows == [["clip", "source", "source_timestamp", "duration_s", "score"]]

    def test_score_format_three_decimal_places(self, tmp_path):
        clips = [(tmp_path / "c.mp4", Segment(0.0, 30.0, 0.123456))]
        write_manifest(tmp_path, Path("/s.wav"), clips)
        with (tmp_path / "manifest.csv").open(newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert rows[1][4] == "0.123", "score must be formatted to 3 decimal places"


# ---------------------------------------------------------------------------
# write_captions_stub
# ---------------------------------------------------------------------------


class TestWriteCaptionsStub:
    def _make_clips(self, out_dir: Path) -> list[tuple[Path, Segment]]:
        return [
            (out_dir / "clip_001.mp4", Segment(0.0, 30.0, 0.95)),
            (out_dir / "clip_002.mp4", Segment(5400.0, 60.0, 0.80)),
        ]

    def test_creates_captions_md(self, tmp_path):
        clips = self._make_clips(tmp_path)
        result = write_captions_stub(tmp_path, clips)
        assert result == tmp_path / "captions.md"
        assert result.exists()

    def test_starts_with_h1_heading(self, tmp_path):
        write_captions_stub(tmp_path, self._make_clips(tmp_path))
        text = (tmp_path / "captions.md").read_text(encoding="utf-8")
        assert text.startswith("# Captions")

    def test_h2_section_per_clip(self, tmp_path):
        write_captions_stub(tmp_path, self._make_clips(tmp_path))
        text = (tmp_path / "captions.md").read_text(encoding="utf-8")
        assert "## clip_001.mp4" in text
        assert "## clip_002.mp4" in text

    def test_source_timestamp_in_section(self, tmp_path):
        write_captions_stub(tmp_path, self._make_clips(tmp_path))
        text = (tmp_path / "captions.md").read_text(encoding="utf-8")
        # clip_001 starts at 0s → 00:00:00
        assert "- source timestamp: 00:00:00" in text
        # clip_002 starts at 5400s = 1h30m → 01:30:00
        assert "- source timestamp: 01:30:00" in text

    def test_caption_and_hashtags_placeholders(self, tmp_path):
        write_captions_stub(tmp_path, self._make_clips(tmp_path))
        text = (tmp_path / "captions.md").read_text(encoding="utf-8")
        assert "- caption: " in text
        assert "- hashtags: " in text

    def test_empty_clips_list(self, tmp_path):
        result = write_captions_stub(tmp_path, [])
        text = result.read_text(encoding="utf-8")
        assert "# Captions" in text
        assert "##" not in text  # no section headings for zero clips

    def test_utf8_encoding(self, tmp_path):
        # Clip name with unicode characters — path stem comes from the clip_path name.
        clips = [(tmp_path / "Góa_clip.mp4", Segment(10.0, 30.0, 0.5))]
        write_captions_stub(tmp_path, clips)
        text = (tmp_path / "captions.md").read_text(encoding="utf-8")
        assert "Góa_clip.mp4" in text
