"""Mac app bundle builder — `releases macapp` wraps the web app in a
double-clickable .app: launcher restarts the server (never serves stale code),
waits for it, then opens a Chrome app-mode window (falls back to the default
browser). Pure stdlib; icon is best-effort via sips/iconutil."""

from __future__ import annotations

import plistlib
import zlib

from typer.testing import CliRunner

from releases import macapp as mac
from releases.cli import app as cli_app


def test_vinyl_png_is_valid_png(tmp_path):
    p = tmp_path / "icon.png"
    mac.write_vinyl_png(p, 64)
    data = p.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"          # PNG signature
    assert b"IHDR" in data and b"IDAT" in data and data.rstrip().endswith(b"IEND\xaeB`\x82")
    # the zlib stream decompresses to the right raw size (64 rows of 1+64*4 bytes)
    start = data.index(b"IDAT") + 4
    raw = zlib.decompress(data[start:data.index(b"IEND") - 8])
    assert len(raw) == 64 * (1 + 64 * 4)


def test_build_app_bundle_structure(tmp_path):
    bundle = mac.build_app(tmp_path, port=8765, artist="Dibs")
    assert bundle == tmp_path / "releases.app"
    plist = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    assert plist["CFBundleExecutable"] == "launcher"
    assert plist["CFBundleIdentifier"].startswith("net.music-ops.")
    assert plist["LSUIElement"] is True               # wrapper leaves no dock residue
    launcher = bundle / "Contents" / "MacOS" / "launcher"
    assert launcher.exists() and launcher.stat().st_mode & 0o111  # executable
    sh = launcher.read_text()
    assert "releases" in sh and "PORT=8765" in sh and '--artist "Dibs"' in sh
    assert "--app=" in sh                             # Chrome app-mode window
    assert "open " in sh                              # default-browser fallback
    assert "PIDFILE" in sh and "kill" in sh           # stale server always replaced
    assert "curl" in sh                               # waits for the server to come up


def test_build_app_no_artist_omits_flag(tmp_path):
    bundle = mac.build_app(tmp_path, port=9000, artist="")
    sh = (bundle / "Contents" / "MacOS" / "launcher").read_text()
    assert "--artist" not in sh and "PORT=9000" in sh


def test_macapp_cli_command(tmp_path):
    r = CliRunner().invoke(cli_app, ["macapp", "--dest", str(tmp_path), "--artist", "Dibs"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "releases.app" / "Contents" / "MacOS" / "launcher").exists()
    assert "releases.app" in r.output
