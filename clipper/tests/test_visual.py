"""Tests for the visual-activity signal: analyze.blend_visual (pure) and
media.visual_activity (parses a real ffmpeg metadata pass on a tiny video)."""

from __future__ import annotations

import shutil

import numpy as np
import pytest

from clipper.analyze import blend_visual
from clipper.media import visual_activity


class TestBlendVisual:
    def test_empty_visual_returns_audio_unchanged(self):
        audio = np.array([0.1, 0.9, 0.3])
        out = blend_visual(audio, [])
        assert np.array_equal(out, audio)

    def test_empty_audio_returns_audio(self):
        audio = np.array([])
        assert blend_visual(audio, [1.0, 2.0]).size == 0

    def test_visual_shifts_the_ranking(self):
        # Audio favours second 0; visual strongly favours second 2.
        audio = np.array([0.9, 0.5, 0.4])
        visual = [0.0, 0.0, 100.0]
        out = blend_visual(audio, visual, visual_weight=0.6)
        assert out.argmax() == 2, "a strong visual moment should win when weighted high"

    def test_zero_weight_is_audio_only(self):
        audio = np.array([0.9, 0.1, 0.5])
        out = blend_visual(audio, [0.0, 100.0, 0.0], visual_weight=0.0)
        assert np.allclose(out, audio)

    def test_length_mismatch_truncates_to_shorter(self):
        audio = np.array([0.5, 0.5, 0.5, 0.5, 0.5])
        out = blend_visual(audio, [1.0, 1.0])  # visual shorter
        assert out.shape[0] == 2

    def test_output_stays_in_unit_range(self):
        audio = np.array([0.2, 0.8, 0.5])
        out = blend_visual(audio, [10.0, 50.0, 90.0], visual_weight=0.4)
        assert out.min() >= 0.0 and out.max() <= 1.0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestVisualActivity:
    def test_parses_per_second_signal_from_a_real_video(self, tmp_path):
        import subprocess

        video = tmp_path / "clip.mp4"
        subprocess.run(
            [
                "ffmpeg", "-v", "error",
                "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30",
                "-t", "3", "-pix_fmt", "yuv420p", "-y", str(video),
            ],
            check=True,
            capture_output=True,
        )
        signal = visual_activity(video, tmp_path, fps=4)
        assert len(signal) >= 2, "a 3s video should yield a few per-second values"
        assert all(v >= 0.0 for v in signal)
        # testsrc2 is in constant motion, so at least one second has real activity.
        assert max(signal) > 0.0
