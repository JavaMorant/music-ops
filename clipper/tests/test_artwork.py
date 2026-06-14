"""Tests for clipper.artwork — cover_box geometry and render_specs output.

All image work uses tiny in-memory images created with Pillow; no real artwork
files are accessed. Specs from SPECS are used as dimension cases.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipper.artwork import SPECS, cover_box, render_specs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_image(tmp_path: Path, width: int, height: int, name: str = "test.jpg") -> Path:
    """Write a solid-colour JPEG of the given size into tmp_path."""
    from PIL import Image

    img = Image.new("RGB", (width, height), color=(128, 64, 32))
    p = tmp_path / name
    img.save(p, format="JPEG", quality=95)
    return p


def _box_w(box: tuple[int, int, int, int]) -> int:
    return box[2] - box[0]


def _box_h(box: tuple[int, int, int, int]) -> int:
    return box[3] - box[1]


# ---------------------------------------------------------------------------
# cover_box — geometry
# ---------------------------------------------------------------------------


class TestCoverBoxGeometry:
    def test_square_source_square_dest_identity(self):
        """Square src → square dst: full source used, box is the whole image."""
        box = cover_box(200, 200, 200, 200)
        assert box == (0, 0, 200, 200)

    def test_square_source_square_dest_different_size(self):
        """100x100 src, 50x50 dst (same aspect): full source."""
        box = cover_box(100, 100, 50, 50)
        assert box == (0, 0, 100, 100)

    def test_wide_source_cropped_on_sides(self):
        """Source 400x200 (2:1) → dest 100x100 (1:1): sides cropped."""
        box = cover_box(400, 200, 100, 100)
        w = _box_w(box)
        h = _box_h(box)
        # Should be a square crop of the 200px height.
        assert h == 200
        assert w == 200
        # Centred horizontally.
        assert box[0] == 100  # left = (400 - 200) // 2
        assert box[1] == 0
        assert box[2] == 300
        assert box[3] == 200

    def test_tall_source_cropped_top_bottom(self):
        """Source 200x400 (1:2) → dest 100x100 (1:1): top/bottom cropped."""
        box = cover_box(200, 400, 100, 100)
        w = _box_w(box)
        h = _box_h(box)
        assert w == 200
        assert h == 200
        # Centred vertically.
        assert box[0] == 0
        assert box[1] == 100  # top = (400 - 200) // 2
        assert box[2] == 200
        assert box[3] == 300

    def test_exact_aspect_match_returns_full_box(self):
        """When src and dst have the exact same aspect ratio, the whole source is used."""
        # 3:2 source → 3:2 dest
        box = cover_box(300, 200, 150, 100)
        assert box == (0, 0, 300, 200)

    def test_resulting_box_aspect_matches_dest_within_1px(self):
        """Crop box aspect must match destination aspect within 1-pixel rounding."""
        src_w, src_h = 1920, 1080
        dst_w, dst_h = 1080, 1920  # portrait story spec
        box = cover_box(src_w, src_h, dst_w, dst_h)
        bw = _box_w(box)
        bh = _box_h(box)
        # box_w / box_h should approximate dst_w / dst_h
        expected_aspect = dst_w / dst_h
        actual_aspect = bw / bh
        assert abs(actual_aspect - expected_aspect) < 1 / min(bw, bh) + 0.01

    def test_box_always_within_source_bounds(self):
        """The crop box must never exceed the source dimensions."""
        cases = [
            (3000, 2000, 1080, 1920),
            (1920, 1080, 3000, 3000),
            (500, 500, 1500, 500),
            (100, 100, 1000, 1000),
            (4000, 3000, 1000, 1000),
        ]
        for src_w, src_h, dst_w, dst_h in cases:
            box = cover_box(src_w, src_h, dst_w, dst_h)
            assert box[0] >= 0, f"left < 0 for {src_w}x{src_h} → {dst_w}x{dst_h}"
            assert box[1] >= 0, f"top < 0 for {src_w}x{src_h} → {dst_w}x{dst_h}"
            assert box[2] <= src_w, f"right > src_w for {src_w}x{src_h} → {dst_w}x{dst_h}"
            assert box[3] <= src_h, f"bottom > src_h for {src_w}x{src_h} → {dst_w}x{dst_h}"

    def test_spec_square_dims(self):
        """Use SPECS['square'] = (3000, 3000) as a destination case."""
        dst_w, dst_h = SPECS["square"]
        box = cover_box(4000, 3000, dst_w, dst_h)
        bw = _box_w(box)
        bh = _box_h(box)
        # Box must be square (within rounding)
        assert abs(bw - bh) <= 1

    def test_spec_story_dims_wide_source(self):
        """3000x3000 square source → story (1080x1920): tall crop out of square."""
        dst_w, dst_h = SPECS["story"]
        box = cover_box(3000, 3000, dst_w, dst_h)
        bw = _box_w(box)
        bh = _box_h(box)
        # The crop should be portrait-shaped (taller than wide).
        assert bh > bw
        # Must stay inside source.
        assert box[0] >= 0 and box[1] >= 0
        assert box[2] <= 3000 and box[3] <= 3000

    def test_spec_banner_dims(self):
        """3000x3000 source → banner (1500x500): very wide crop."""
        dst_w, dst_h = SPECS["banner"]
        box = cover_box(3000, 3000, dst_w, dst_h)
        bw = _box_w(box)
        bh = _box_h(box)
        assert bw > bh  # wide crop

    def test_spec_profile_dims(self):
        dst_w, dst_h = SPECS["profile"]
        box = cover_box(3000, 3000, dst_w, dst_h)
        bw = _box_w(box)
        bh = _box_h(box)
        # profile is 1000x1000 (square)
        assert abs(bw - bh) <= 1

    @pytest.mark.parametrize("spec_name,dst_dims", list(SPECS.items()))
    def test_all_specs_box_within_source(self, spec_name, dst_dims):
        """For each spec, the crop box from a 3000x3000 source is within bounds."""
        src_w = src_h = 3000
        dst_w, dst_h = dst_dims
        box = cover_box(src_w, src_h, dst_w, dst_h)
        assert box[0] >= 0
        assert box[1] >= 0
        assert box[2] <= src_w
        assert box[3] <= src_h

    @pytest.mark.parametrize("spec_name,dst_dims", list(SPECS.items()))
    def test_all_specs_resulting_aspect_within_tolerance(self, spec_name, dst_dims):
        """The crop box aspect must approximate the destination aspect within rounding."""
        src_w = src_h = 3000
        dst_w, dst_h = dst_dims
        box = cover_box(src_w, src_h, dst_w, dst_h)
        bw = _box_w(box)
        bh = _box_h(box)
        expected = dst_w / dst_h
        actual = bw / bh
        # Allow 1-pixel rounding error on the smaller dimension.
        tolerance = 1.0 / min(bw, bh) + 0.001
        assert abs(actual - expected) <= tolerance, (
            f"spec={spec_name}: box aspect {actual:.4f} != expected {expected:.4f}"
        )


# ---------------------------------------------------------------------------
# render_specs
# ---------------------------------------------------------------------------


class TestRenderSpecs:
    def test_produces_all_four_spec_files(self, tmp_path):
        src = _make_image(tmp_path, 64, 64)
        out_dir = tmp_path / "out"
        results = render_specs(src, out_dir)
        spec_names = {r[0] for r in results}
        assert spec_names == set(SPECS.keys())

    def test_output_files_exist(self, tmp_path):
        src = _make_image(tmp_path, 64, 64)
        out_dir = tmp_path / "out"
        results = render_specs(src, out_dir)
        for name, path, _ in results:
            assert path.exists(), f"output file missing for spec {name!r}: {path}"

    def test_output_dimensions_match_specs(self, tmp_path):
        from PIL import Image

        src = _make_image(tmp_path, 64, 64)
        out_dir = tmp_path / "out"
        results = render_specs(src, out_dir)
        for name, path, _ in results:
            expected_w, expected_h = SPECS[name]
            with Image.open(path) as img:
                assert img.width == expected_w, (
                    f"spec {name!r}: width {img.width} != {expected_w}"
                )
                assert img.height == expected_h, (
                    f"spec {name!r}: height {img.height} != {expected_h}"
                )

    def test_upscaled_flag_true_when_small_source(self, tmp_path):
        """64x64 source → all specs are larger → upscaled must be True for all."""
        src = _make_image(tmp_path, 64, 64)
        out_dir = tmp_path / "out"
        results = render_specs(src, out_dir)
        for name, path, upscaled in results:
            assert upscaled is True, f"spec {name!r}: upscaled should be True for tiny source"

    def test_upscaled_flag_false_when_large_source(self, tmp_path):
        """4000x4000 source → crop box is always >= spec target → upscaled False."""
        src = _make_image(tmp_path, 4000, 4000, name="big.jpg")
        out_dir = tmp_path / "out"
        results = render_specs(src, out_dir)
        for name, path, upscaled in results:
            assert upscaled is False, f"spec {name!r}: upscaled should be False for 4000x4000 source"

    def test_output_files_are_jpeg(self, tmp_path):
        from PIL import Image

        src = _make_image(tmp_path, 64, 64)
        out_dir = tmp_path / "out"
        results = render_specs(src, out_dir)
        for name, path, _ in results:
            assert path.suffix.lower() == ".jpg", f"expected .jpg for {name}, got {path.suffix}"
            with Image.open(path) as img:
                assert img.format == "JPEG", f"expected JPEG format for {name}"

    def test_output_filenames_contain_spec_name_and_dimensions(self, tmp_path):
        src = _make_image(tmp_path, 64, 64)
        out_dir = tmp_path / "out"
        results = render_specs(src, out_dir)
        for name, path, _ in results:
            w, h = SPECS[name]
            assert name in path.name, f"spec name {name!r} not in filename {path.name!r}"
            assert f"{w}x{h}" in path.name, f"{w}x{h} not in filename {path.name!r}"

    def test_output_dir_created_if_not_existing(self, tmp_path):
        src = _make_image(tmp_path, 64, 64)
        out_dir = tmp_path / "nested" / "output"
        assert not out_dir.exists()
        render_specs(src, out_dir)
        assert out_dir.exists()

    def test_square_source_story_spec_upscaled_logic(self, tmp_path):
        """3000x3000 source → story is 1080x1920. Crop box height = 3000,
        width = round(3000 * 1080/1920) = 1687. Both 1687 and 3000 > 1080/1920,
        so the crop box (1687 wide) > spec target width (1080) → not upscaled."""
        src = _make_image(tmp_path, 3000, 3000, name="cover.jpg")
        out_dir = tmp_path / "out"
        results = render_specs(src, out_dir)
        by_name = {r[0]: r for r in results}
        _, _, upscaled = by_name["story"]
        # crop box width (1687) < dest width (1080)? No — 1687 > 1080.
        # upscaled = (box[2]-box[0]) < w  →  1687 < 1080 → False
        assert upscaled is False

    def test_4000x4000_source_all_not_upscaled(self, tmp_path):
        """4000x4000 is larger than every spec's max dimension → never upscaled."""
        src = _make_image(tmp_path, 4000, 4000, name="large.jpg")
        out_dir = tmp_path / "out"
        results = render_specs(src, out_dir)
        for name, _, upscaled in results:
            assert upscaled is False, f"4000x4000 source → {name} should not be upscaled"

    def test_rgb_source_accepted(self, tmp_path):
        """Pillow RGB source should be handled without error."""
        from PIL import Image

        p = tmp_path / "rgba.png"
        Image.new("RGBA", (100, 100), (255, 0, 0, 200)).save(p, format="PNG")
        out_dir = tmp_path / "out_rgba"
        # Should not raise; render_specs converts to RGB internally.
        results = render_specs(p, out_dir)
        assert len(results) == 4

    def test_returns_list_of_triples(self, tmp_path):
        src = _make_image(tmp_path, 64, 64)
        results = render_specs(src, tmp_path / "out")
        assert isinstance(results, list)
        for item in results:
            assert len(item) == 3
            name, path, upscaled = item
            assert isinstance(name, str)
            assert isinstance(path, Path)
            assert isinstance(upscaled, bool)

    def test_non_image_file_raises_artwork_error(self, tmp_path):
        """A non-image file (UnidentifiedImageError, an OSError subclass) is
        translated to a clean ArtworkError rather than escaping as a traceback."""
        from clipper.artwork import ArtworkError

        fake = tmp_path / "not_an_image.jpg"
        fake.write_text("this is plainly not a JPEG", encoding="utf-8")
        with pytest.raises(ArtworkError):
            render_specs(fake, tmp_path / "out")

    @pytest.mark.parametrize("spec_name,expected_dims", list(SPECS.items()))
    def test_each_spec_output_has_correct_pixels(self, tmp_path, spec_name, expected_dims):
        from PIL import Image

        src = _make_image(tmp_path, 64, 64, name=f"src_{spec_name}.jpg")
        out_dir = tmp_path / f"out_{spec_name}"
        results = render_specs(src, out_dir)
        by_name = {r[0]: r for r in results}
        assert spec_name in by_name, f"spec {spec_name!r} missing from results"
        _, path, _ = by_name[spec_name]
        with Image.open(path) as img:
            assert (img.width, img.height) == expected_dims, (
                f"spec {spec_name!r}: got {img.width}x{img.height}, expected {expected_dims}"
            )
