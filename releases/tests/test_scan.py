import hashlib
import os
from pathlib import Path

from releases.scan import scan


def _by_name(projects):
    return {p.name: p for p in projects}


def _tree_digest(root: Path) -> dict[str, str]:
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = Path(dirpath) / f
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class TestScan:
    def test_finds_real_projects_and_prunes_noise(self, synth_library):
        projects = _by_name(scan(synth_library))
        # real projects present
        assert "Encara" in projects
        assert "joonya 137 Cmin" in projects
        assert "PinkPanther" in projects
        # sample-pack / preset / backup noise pruned
        assert "demo" not in projects
        assert "Encara_autosave" not in projects
        assert not any("Drum Kits" in p.path for p in projects.values())

    def test_multi_version_folder_is_one_project(self, synth_library):
        enc = _by_name(scan(synth_library))["Encara"]
        assert enc.kind == "project"
        assert enc.flp_count == 2          # both .flp versions grouped
        assert enc.stage == "complete"
        assert enc.bpm == 129 and enc.key == "F"

    def test_bounce_detection(self, synth_library):
        p = _by_name(scan(synth_library))
        assert p["Encara"].has_bounce is True          # sibling render
        assert p["rmx1"].has_bounce is True            # exports/ subfolder
        assert p["sketch1"].has_bounce is False        # only Audio/ stems

    def test_standalone_bounce_with_no_flp(self, synth_library):
        p = _by_name(scan(synth_library))["Cha Cha Slide (Dibs)_140_Dmin"]
        assert p.kind == "bounce"
        assert p.flp_count == 0
        assert p.has_bounce is True
        assert p.stage == "track-list"
        assert p.bpm == 140 and p.key == "Dm"

    def test_standalone_root_flp(self, synth_library):
        p = _by_name(scan(synth_library))["PinkPanther"]
        assert p.kind == "project"
        assert p.stage == "uncategorized"

    def test_standalone_flp_with_sibling_render_has_bounce(self, tmp_path):
        # a loose .flp in a structural folder + a same-stem render → has_bounce
        root = tmp_path / "projects"
        tracks = root / "Beats" / "Tracks"
        (tracks).mkdir(parents=True)
        (tracks / "loosey.flp").write_bytes(b"x")
        (tracks / "loosey.mp3").write_bytes(b"x")
        (tracks / "unrelated.mp3").write_bytes(b"x")  # a different project's render
        p = _by_name(scan(root))
        assert p["loosey"].kind == "project"
        assert p["loosey"].has_bounce is True
        # the unrelated render is its own standalone bounce, not loosey's
        assert p["unrelated"].kind == "bounce"

    def test_scan_never_writes_to_library(self, synth_library):
        before = _tree_digest(synth_library)
        scan(synth_library)
        scan(synth_library)  # twice — still read-only
        assert _tree_digest(synth_library) == before


def _tree_meta(root: Path) -> dict[str, tuple[int, float]]:
    """(size, mtime) per file — proves no write/modify without reading bytes."""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = Path(dirpath) / f
            try:
                st = p.stat()
                out[str(p.relative_to(root))] = (st.st_size, st.st_mtime)
            except OSError:
                pass
    return out


class TestScanReal:
    def test_real_library_is_untouched(self, real_library):
        before = _tree_meta(real_library)
        projects = scan(real_library)
        assert len(projects) > 50               # the real ~150+ idle projects
        assert _tree_meta(real_library) == before
