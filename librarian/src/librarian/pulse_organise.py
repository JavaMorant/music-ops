"""Export a stick's play-history crates as rekordbox-importable playlists.

We never touch the stick's ``export.pdb`` — only rekordbox can safely write one.
Instead we write ``.m3u8`` playlists (+ a plain tracklist) to the laptop; you
import them into rekordbox, assign them to the stick, and re-export. rekordbox
then writes a valid pdb and the crates show on the CDJ.
"""
from __future__ import annotations

import re
from pathlib import Path

DIR = Path.home() / "DJ" / ".librarian" / "usb-crates"

README = (
    "PULSE CRATES — organise this stick by what you actually play\n"
    "===========================================================\n\n"
    "Built from the stick's rekordbox play history. We never edit the stick's\n"
    "database (only rekordbox can safely write export.pdb), so to get these onto\n"
    "the CDJ:\n\n"
    "  1. rekordbox > File > Import > Import Playlist, and pick the .m3u8 files in\n"
    "     this folder (or drag them into the Playlists tree).\n"
    "  2. Assign the imported playlists to your USB device.\n"
    "  3. Export to the device — rekordbox writes export.pdb; the crates appear\n"
    "     on the CDJ.\n\n"
    "Each .m3u8 lists tracks in play-priority order. The matching .txt is the\n"
    "same list in plain text (handy if a track has no on-stick path and you need\n"
    "to add it by searching the title).\n"
)


def _safe(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w .&!-]", " ", name)).strip() or "crate"


def write_crates(stick: str, crates: list[dict]) -> dict:
    """Write each crate as ``<DIR>/<stick>/<name>.m3u8`` (+ ``.txt``) and a README.
    Returns a summary; ``needs_paths`` flags crates that lacked on-stick paths."""
    out = DIR / _safe(stick)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for c in crates:
        tracks = c.get("tracks", [])
        if not tracks:
            continue
        base = _safe(c["name"])
        m3u, txt, have_path = ["#EXTM3U"], [], 0
        for t in tracks:
            label = t.get("label", "")
            m3u.append(f"#EXTINF:-1,{label}")
            if t.get("path"):
                m3u.append(t["path"])
                have_path += 1
            txt.append(label)
        (out / f"{base}.m3u8").write_text("\n".join(m3u) + "\n", encoding="utf-8")
        (out / f"{base}.txt").write_text("\n".join(txt) + "\n", encoding="utf-8")
        written.append({"name": c["name"], "tracks": len(tracks), "with_path": have_path})
    (out / "README.txt").write_text(README, encoding="utf-8")
    return {"folder": str(out), "crates": written,
            "needs_paths": any(w["with_path"] < w["tracks"] for w in written)}
