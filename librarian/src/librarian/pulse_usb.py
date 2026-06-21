"""Read CDJ-stick play history (PIONEER/rekordbox/export.pdb) via rekordcrate and
turn it into DJ-play analytics. The user plays mainly off USB, so the real,
*ordered* play log lives on the stick — letting us see not just what gets played,
but how sets are built: recurring transitions, openers, closers, hot vs cold.
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
    out = []
    vols = Path("/Volumes")
    if vols.exists():
        for v in vols.iterdir():
            pdb = v / "PIONEER" / "rekordbox" / "export.pdb"
            if pdb.exists():
                out.append((v, pdb))
    return out


def parse_pdb(pdb: Path):
    """(tracks, sessions): tracks[id]={title,artist,genre}; sessions = play log in
    chronological order, each a list of track-ids in the order they were played."""
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
    # history entries -> ordered sessions (dedupe the triple-printed rows)
    by_pl, seen = collections.defaultdict(list), set()
    for m in re.finditer(r"HistoryEntry \{ track_id: TrackId\((\d+)\), playlist_id: HistoryPlaylistId\((\d+)\), entry_index: (\d+) \}", txt):
        tid, pid, idx = int(m[1]), int(m[2]), int(m[3])
        if (pid, idx) in seen:
            continue
        seen.add((pid, idx))
        by_pl[pid].append((idx, tid))
    sessions = [[tid for _, tid in sorted(by_pl[pid])] for pid in sorted(by_pl)]
    sessions = [s for s in sessions if s]
    return tracks, sessions


def usb_insights(vol: Path, pdb: Path, last_n: int = 50) -> dict:
    tracks, sessions = parse_pdb(pdb)
    recent = sessions[-last_n:] if last_n else sessions
    recent_ids = {tid for s in recent for tid in s}
    plays = collections.Counter(tid for s in sessions for tid in s)
    recent_plays = collections.Counter(tid for s in recent for tid in s)

    def lbl(tid):
        t = tracks.get(tid)
        return f"{t['artist']} - {t['title']}".strip(" -") if t else f"<{tid}>"

    # transitions: consecutive A->B pairs that recur across recent sets
    trans = collections.Counter()
    for s in recent:
        for a, b in zip(s, s[1:]):
            if a != b:
                trans[(a, b)] += 1
    transitions = [{"from": lbl(a), "to": lbl(b), "count": n}
                   for (a, b), n in trans.most_common(8) if n > 1]

    openers = collections.Counter(s[0] for s in recent if s)
    closers = collections.Counter(s[-1] for s in recent if s)

    # cold: was a staple, but absent from your recent sets
    cold = [{"label": lbl(tid), "plays": plays[tid]}
            for tid, _ in plays.most_common() if tid not in recent_ids][:12]

    gl = collections.Counter(tracks[t]["genre"] for t in recent_ids if tracks.get(t, {}).get("genre"))
    avg = round(sum(len(s) for s in recent) / max(1, len(recent)), 1)
    return {
        "name": vol.name,
        "tracks": len(tracks),
        "plays": sum(plays.values()),
        "sessions": len(sessions),
        "window": len(recent),
        "avg_per_set": avg,
        "distinct_played": len(plays),
        "untouched": len(tracks) - len(plays),
        "coverage_pct": round(100 * len(plays) / max(1, len(tracks))),
        "hot": [{"label": lbl(t), "plays": n} for t, n in recent_plays.most_common(15)],
        "cold": cold,
        "transitions": transitions,
        "openers": [{"label": lbl(t), "count": n} for t, n in openers.most_common(6)],
        "closers": [{"label": lbl(t), "count": n} for t, n in closers.most_common(6)],
        "genre_lean": [{"genre": g, "count": c} for g, c in gl.most_common(8)],
    }
