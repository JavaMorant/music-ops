"""Read CDJ-stick play history from a device export (PIONEER/rekordbox/export.pdb)
via rekordcrate. Used by ``pulse`` and the web GUI — the user mainly plays off
USB, so the real play log lives on the stick, not the laptop.
"""
from __future__ import annotations

import collections
import re
import subprocess
from pathlib import Path

RC = str(Path.home() / ".cargo" / "bin" / "rekordcrate")
_STR = r'"((?:[^"\\]|\\.)*)"'


def available() -> bool:
    return Path(RC).exists()


def _unesc(s):
    return s.encode().decode("unicode_escape", errors="replace") if "\\" in s else s


def find_usbs():
    """[(volume, export.pdb)] for every mounted stick with a rekordbox export."""
    out = []
    vols = Path("/Volumes")
    if vols.exists():
        for v in vols.iterdir():
            pdb = v / "PIONEER" / "rekordbox" / "export.pdb"
            if pdb.exists():
                out.append((v, pdb))
    return out


def parse_pdb(pdb: Path):
    """(tracks, plays): tracks[id] = {title, artist, genre}; plays[id] = count."""
    txt = subprocess.run([RC, "dump-pdb", str(pdb)], capture_output=True, text=True).stdout
    artists, genres = {}, {}
    for m in re.finditer(r"Artist\(Artist \{(.*?)\}\)", txt):
        b = m.group(1)
        i, n = re.search(r"\bid: ArtistId\((\d+)\)", b), re.search(r"name: DeviceSQLString\(" + _STR + r"\)", b)
        if i and n:
            artists[int(i.group(1))] = _unesc(n.group(1))
    for m in re.finditer(r"Genre\(Genre \{(.*?)\}\)", txt):
        b = m.group(1)
        i, n = re.search(r"\bid: GenreId\((\d+)\)", b), re.search(r"name: DeviceSQLString\(" + _STR + r"\)", b)
        if i and n:
            genres[int(i.group(1))] = _unesc(n.group(1))
    tracks = {}
    for m in re.finditer(r"Track\(Track \{(.*?)\}\)", txt):
        b = m.group(1)
        i = re.search(r", id: TrackId\((\d+)\)", b)
        t = re.search(r"title: DeviceSQLString\(" + _STR + r"\)", b)
        a = re.search(r", artist_id: ArtistId\((\d+)\)", b)
        g = re.search(r"genre_id: GenreId\((\d+)\)", b)
        if i and t:
            tracks[int(i.group(1))] = {
                "title": _unesc(t.group(1)),
                "artist": artists.get(int(a.group(1)) if a else 0, ""),
                "genre": genres.get(int(g.group(1)) if g else 0, ""),
            }
    plays, seen = collections.Counter(), set()
    for m in re.finditer(r"HistoryEntry \{ track_id: TrackId\((\d+)\), playlist_id: HistoryPlaylistId\((\d+)\), entry_index: (\d+) \}", txt):
        tid, pid, idx = int(m[1]), int(m[2]), int(m[3])
        if (pid, idx) in seen:
            continue
        seen.add((pid, idx))
        plays[tid] += 1
    return tracks, plays


def usb_insights(vol: Path, pdb: Path) -> dict:
    tracks, plays = parse_pdb(pdb)
    played = [t for t in tracks if plays.get(t, 0) > 0]

    def lbl(t):
        return f"{t['artist']} - {t['title']}".strip(" -")

    gl = collections.Counter(tracks[t]["genre"] for t in played if tracks[t]["genre"])
    return {
        "name": vol.name,
        "tracks": len(tracks),
        "plays": sum(plays.values()),
        "distinct_played": len(played),
        "untouched": len(tracks) - len(played),
        "coverage_pct": round(100 * len(played) / max(1, len(tracks))),
        "top": [{"label": lbl(tracks[t]), "plays": n} for t, n in plays.most_common(15) if t in tracks],
        "genre_lean": [{"genre": g, "count": c} for g, c in gl.most_common(8)],
    }
