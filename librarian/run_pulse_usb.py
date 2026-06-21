"""pulse, USB-native — reads play history straight off your CDJ sticks.

You mainly play off USBs, so the real play log lives on the stick (the CDJs write
it to PIONEER/rekordbox/export.pdb). This auto-detects mounted sticks, parses
each export.pdb via rekordcrate, and reports staples / coverage / untouched +
writes an Untouched.m3u — all from what you ACTUALLY played out, not the laptop.

  python run_pulse_usb.py                 # all mounted sticks
  python run_pulse_usb.py /Volumes/NAME    # one stick
"""
from __future__ import annotations

import collections
import os
import re
import subprocess
import sys
from pathlib import Path

RC = str(Path.home() / ".cargo" / "bin" / "rekordcrate")
STR = r'"((?:[^"\\]|\\.)*)"'
OUT_BASE = Path.home() / "DJ" / "pulse-usb"


def unesc(s):
    return s.encode().decode("unicode_escape", errors="replace") if "\\" in s else s


def find_usbs():
    out = []
    for v in Path("/Volumes").iterdir() if Path("/Volumes").exists() else []:
        pdb = v / "PIONEER" / "rekordbox" / "export.pdb"
        if pdb.exists():
            out.append((v, pdb))
    return out


def parse_pdb(pdb: Path):
    txt = subprocess.run([RC, "dump-pdb", str(pdb)], capture_output=True, text=True).stdout
    artists = {}
    for m in re.finditer(r"Artist\(Artist \{(.*?)\}\)", txt):
        b = m.group(1)
        i, n = re.search(r"\bid: ArtistId\((\d+)\)", b), re.search(r"name: DeviceSQLString\(" + STR + r"\)", b)
        if i and n:
            artists[int(i.group(1))] = unesc(n.group(1))
    genres = {}
    for m in re.finditer(r"Genre\(Genre \{(.*?)\}\)", txt):
        b = m.group(1)
        i, n = re.search(r"\bid: GenreId\((\d+)\)", b), re.search(r"name: DeviceSQLString\(" + STR + r"\)", b)
        if i and n:
            genres[int(i.group(1))] = unesc(n.group(1))
    tracks = {}
    for m in re.finditer(r"Track\(Track \{(.*?)\}\)", txt):
        b = m.group(1)
        i = re.search(r", id: TrackId\((\d+)\)", b)
        t = re.search(r"title: DeviceSQLString\(" + STR + r"\)", b)
        a = re.search(r", artist_id: ArtistId\((\d+)\)", b)
        g = re.search(r"genre_id: GenreId\((\d+)\)", b)
        fp = re.search(r"file_path: DeviceSQLString\(" + STR + r"\)", b)
        if i and t:
            tid = int(i.group(1))
            tracks[tid] = {"title": unesc(t.group(1)),
                           "artist": artists.get(int(a.group(1)) if a else 0, ""),
                           "genre": genres.get(int(g.group(1)) if g else 0, ""),
                           "path": unesc(fp.group(1)) if fp else ""}
    plays = collections.Counter()
    seen = set()
    for m in re.finditer(r"HistoryEntry \{ track_id: TrackId\((\d+)\), playlist_id: HistoryPlaylistId\((\d+)\), entry_index: (\d+) \}", txt):
        tid, pid, idx = int(m[1]), int(m[2]), int(m[3])
        if (pid, idx) in seen:
            continue
        seen.add((pid, idx))
        plays[tid] += 1
    return tracks, plays


def report(vol: Path, tracks, plays):
    played = [tid for tid in tracks if plays.get(tid, 0) > 0]
    untouched = [tid for tid in tracks if plays.get(tid, 0) == 0]
    lbl = lambda t: f"{t['artist']} - {t['title']}".strip(" -")
    out_dir = OUT_BASE / vol.name
    out_dir.mkdir(parents=True, exist_ok=True)

    L = [f"# USB Pulse — {vol.name}", "",
         f"- **Tracks on stick:** {len(tracks)}",
         f"- **Plays logged:** {sum(plays.values())}  across {len(played)} distinct tracks",
         f"- **Coverage:** {100 * len(played) // max(1, len(tracks))}% played · "
         f"**{len(untouched)} never played** off this stick", "",
         "## 🔥 Most played off this stick", ""]
    for tid, n in plays.most_common(20):
        if tid in tracks:
            L.append(f"{n:3d}×  {lbl(tracks[tid])}")
    gl = collections.Counter(tracks[t]["genre"] for t in played if tracks[t]["genre"])
    if gl:
        L += ["", "## 🎚️ What you actually play (by genre)", ""]
        L += [f"- {g}: {c}" for g, c in gl.most_common(10)]

    # Untouched.m3u — prepend the USB mount to each on-device path
    paths = []
    for tid in untouched:
        p = tracks[tid]["path"]
        if p:
            paths.append(str(vol) + p if p.startswith("/") else str(vol / p))
    (out_dir / "Untouched.m3u").write_text("#EXTM3U\n" + "\n".join(paths) + "\n", encoding="utf-8")
    (out_dir / "pulse-report.md").write_text("\n".join(L), encoding="utf-8")
    return out_dir, len(tracks), sum(plays.values()), len(untouched)


def main():
    if not Path(RC).exists():
        print("rekordcrate not found — run: cargo install rekordcrate")
        return
    targets = [(Path(sys.argv[1]), Path(sys.argv[1]) / "PIONEER/rekordbox/export.pdb")] if len(sys.argv) > 1 else find_usbs()
    if not targets:
        print("No mounted USB with PIONEER/rekordbox/export.pdb found. Plug a stick in.")
        return
    for vol, pdb in targets:
        if not pdb.exists():
            print(f"  {vol.name}: no export.pdb"); continue
        tracks, plays = parse_pdb(pdb)
        out_dir, nt, np_, nu = report(vol, tracks, plays)
        print(f"✓ {vol.name}: {nt} tracks · {np_} plays · {nu} untouched  ->  {out_dir}")


if __name__ == "__main__":
    main()
