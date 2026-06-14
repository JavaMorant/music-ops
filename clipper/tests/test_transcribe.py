"""Tests for the ambition-tier transcription helper (transcribe.py).

faster-whisper is not present in CI, so these cover availability detection and
the pure window-mapping helper; the model-loading path is exercised only when
the package is installed.
"""

from __future__ import annotations

from clipper.transcribe import TranscriptSegment, is_available, transcript_for_window


def test_is_available_is_boolean():
    assert isinstance(is_available(), bool)


def _segments():
    return [
        TranscriptSegment(0.0, 5.0, "intro"),
        TranscriptSegment(5.0, 10.0, "the drop"),
        TranscriptSegment(10.0, 15.0, "outro"),
    ]


def test_window_picks_overlapping_segments():
    # [4, 11) overlaps all three (4<5 intro, the drop, 10<11 outro)
    assert transcript_for_window(_segments(), 4.0, 11.0) == "intro the drop outro"


def test_window_excludes_non_overlapping():
    # [5, 10) overlaps only "the drop" (boundaries are half-open)
    assert transcript_for_window(_segments(), 5.0, 10.0) == "the drop"


def test_window_with_no_segments():
    assert transcript_for_window([], 0.0, 30.0) == ""


def test_window_outside_all_segments():
    assert transcript_for_window(_segments(), 100.0, 130.0) == ""
