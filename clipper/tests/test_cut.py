"""Tests for cut.py arg-building and the two fixes:
  1. 9:16 crop is clamped so sources already taller than 9:16 don't crash ffmpeg.
  2. audio-only output is pinned to AAC (.m4a), not the raw source extension.

Pure tests assert the constructed ffmpeg args; integration tests (skipped when
ffmpeg is absent) actually cut generated media and check the real output.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
import typer

from clipper.analyze import Segment
from clipper.cli import _output_ext, _validate_x_offset
from clipper.cut import CROP_H, CROP_W, _audio_args, _crop_scale_filter, _video_args

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# --------------------------------------------------------------------------- #
# Pure: crop filter is clamped
# --------------------------------------------------------------------------- #


class TestCropScaleFilter:
    def test_crop_width_is_clamped_to_source(self):
        vf = _crop_scale_filter(None)
        # both dimensions clamped with min() so the box never exceeds the source
        assert r"min(iw\,ih*9/16)" in vf
        assert r"min(ih\,iw*16/9)" in vf
        assert "scale=1080:1920" in vf

    def test_centred_by_default(self):
        vf = _crop_scale_filter(None)
        assert "(iw-ow)/2" in vf  # horizontal centre
        assert "(ih-oh)/2" in vf  # vertical centre

    def test_manual_x_offset_is_used(self):
        vf = _crop_scale_filter(100)
        assert ":100:" in vf
        assert "(iw-ow)/2" not in vf

    def test_comma_inside_min_is_escaped(self):
        # an unescaped comma would be read by ffmpeg as a filter separator
        vf = _crop_scale_filter(None)
        assert "min(iw," not in vf  # must be the escaped form only

    def test_crop_constants_carry_escaped_commas(self):
        # Belt-and-suspenders: the module-level CROP_W / CROP_H constants used in
        # the filter string must each contain the escaped form so that even if the
        # filter is assembled differently the invariant holds.
        assert r"\," in CROP_W
        assert r"\," in CROP_H

    def test_near_square_4_5_source_crop_h_is_clamped(self):
        # A 4:5 source (e.g. 1080x1350) causes iw*16/9 = 1920 > ih = 1350, so
        # CROP_H must clamp to ih.  Without the min() fix ffmpeg would abort with
        # "Invalid too big size for height".  We verify the fix is present in the
        # filter string rather than replaying the full ffmpeg call.
        vf = _crop_scale_filter(None)
        # CROP_H = min(ih, iw*16/9) — ih wins for a 4:5 source, so the formula
        # must be present in the filter (the integration test below confirms the
        # real ffmpeg outcome).
        assert r"min(ih\,iw*16/9)" in vf


# --------------------------------------------------------------------------- #
# Pure: audio-only output is AAC, not the source extension
# --------------------------------------------------------------------------- #


class TestArgs:
    def test_audio_args_pin_aac(self):
        args = _audio_args("in.wav", Segment(0.0, 10.0, 0.5), "out.m4a")
        assert "-c:a" in args and "aac" in args
        assert "-b:a" in args and "192k" in args

    def test_video_args_include_crop_and_aac(self):
        args = _video_args("in.mp4", Segment(1.0, 5.0, 0.5), "out.mp4", None)
        vf = args[args.index("-vf") + 1]
        assert vf.startswith("crop=")
        assert "-c:v" in args and "libx264" in args
        assert "aac" in args

    def test_output_ext(self):
        assert _output_ext((1920, 1080)) == ".mp4"
        assert _output_ext(None) == ".m4a"  # audio-only no longer reuses source ext

    def test_video_args_pin_aac_bitrate(self):
        # Regression: _video_args must include both -c:a aac AND -b:a 192k so
        # that the audio track in a video clip is always a consistent bitrate.
        args = _video_args("in.mp4", Segment(0.0, 5.0, 0.9), "out.mp4", None)
        assert "-b:a" in args and "192k" in args


# --------------------------------------------------------------------------- #
# Pure: x-offset validation clamps instead of going negative
# --------------------------------------------------------------------------- #


class TestValidateXOffset:
    def test_landscape_accepts_in_range_offset(self):
        # 1920x1080 → crop_w = 1080*9/16 = 608, limit = 1312
        _validate_x_offset((1920, 1080), 100)  # no raise

    def test_landscape_rejects_too_large_offset(self):
        with pytest.raises(typer.Exit):
            _validate_x_offset((1920, 1080), 5000)

    def test_tall_source_accepts_zero(self):
        # 1080x2400 is taller than 9:16 → crop spans full width, only 0 valid
        _validate_x_offset((1080, 2400), 0)  # no raise (was: negative range bug)

    def test_tall_source_rejects_nonzero(self):
        with pytest.raises(typer.Exit):
            _validate_x_offset((1080, 2400), 50)

    def test_4_5_portrait_accepts_in_range_offset(self):
        # 1080x1350 (4:5): crop_w = min(1080, 1350*9//16) = min(1080, 759) = 759
        # limit = 1080 - 759 = 321; any value in 0..321 is valid.
        _validate_x_offset((1080, 1350), 0)   # no raise
        _validate_x_offset((1080, 1350), 321)  # no raise at the boundary

    def test_4_5_portrait_rejects_out_of_range_offset(self):
        with pytest.raises(typer.Exit):
            _validate_x_offset((1080, 1350), 322)


# --------------------------------------------------------------------------- #
# Integration: actually cut generated media (the regression these fixes guard)
# --------------------------------------------------------------------------- #


def _make_video(path, size, seconds=2):
    subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=15",
            "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
            "-t", str(seconds), "-pix_fmt", "yuv420p", "-y", str(path),
        ],
        check=True, capture_output=True,
    )


def _frame_size(path):
    from clipper.media import video_frame_size

    return video_frame_size(path)


def _audio_codec(path):
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path),
        ],
        check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
class TestRealCut:
    def test_tall_portrait_source_does_not_crash_and_is_1080x1920(self, tmp_path):
        from clipper.cut import cut_segment

        src = tmp_path / "tall.mp4"
        _make_video(src, "1080x2400")  # taller than 9:16 → used to crash ffmpeg
        dest = tmp_path / "clip.mp4"
        cut_segment(src, Segment(0.0, 1.0, 0.9), dest)  # must not raise
        assert dest.exists()
        assert _frame_size(dest) == (1080, 1920)

    def test_4_5_portrait_source_does_not_crash_and_is_1080x1920(self, tmp_path):
        # A 4:5 source (1080x1350) exercises the CROP_H clamp: without min(),
        # iw*16/9 = 1920 > ih = 1350 and ffmpeg aborts with "Invalid too big
        # size for height".  This is a distinct code path from the 1080x2400
        # test which exercises the CROP_W clamp.
        from clipper.cut import cut_segment

        src = tmp_path / "four_five.mp4"
        _make_video(src, "1080x1350")
        dest = tmp_path / "clip.mp4"
        cut_segment(src, Segment(0.0, 1.0, 0.9), dest)  # must not raise
        assert dest.exists()
        assert _frame_size(dest) == (1080, 1920)

    def test_landscape_source_still_cuts_to_1080x1920(self, tmp_path):
        from clipper.cut import cut_segment

        src = tmp_path / "wide.mp4"
        _make_video(src, "1920x1080")
        dest = tmp_path / "clip.mp4"
        cut_segment(src, Segment(0.0, 1.0, 0.9), dest)
        assert _frame_size(dest) == (1080, 1920)

    def test_landscape_source_output_has_aac_audio(self, tmp_path):
        # Regression: _video_args pins AUDIO_CODEC = ["-c:a", "aac", "-b:a", "192k"].
        # Confirm the actual output stream is AAC (not the source's pcm_s16le or
        # whatever testsrc2 produces), so the video clip is always postable.
        from clipper.cut import cut_segment

        src = tmp_path / "wide.mp4"
        _make_video(src, "1920x1080")
        dest = tmp_path / "clip.mp4"
        cut_segment(src, Segment(0.0, 1.0, 0.9), dest)
        assert _audio_codec(dest) == "aac"

    def test_explicit_x_offset_cuts_without_error(self, tmp_path):
        # The manual --x-offset path (x="200" rather than the centred default)
        # is otherwise only checked at the arg-string level; confirm it survives
        # a real cut on a landscape source and still delivers 1080x1920.
        from clipper.cut import cut_segment

        src = tmp_path / "wide.mp4"
        _make_video(src, "1920x1080")
        dest = tmp_path / "clip.mp4"
        cut_segment(src, Segment(0.0, 1.0, 0.9), dest, x_offset=200)
        assert dest.exists()
        assert _frame_size(dest) == (1080, 1920)

    def test_audio_only_output_is_aac(self, tmp_path):
        import numpy as np
        import soundfile as sf

        from clipper.cut import cut_audio_segment

        wav = tmp_path / "set.wav"
        sr = 48000
        t = np.arange(sr * 3) / sr
        tone = (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32")  # loudnorm needs real signal
        sf.write(str(wav), tone, sr)
        dest = tmp_path / "clip.m4a"
        cut_audio_segment(wav, Segment(0.0, 1.0, 0.5), dest)
        assert dest.exists()
        assert _audio_codec(dest) == "aac"
