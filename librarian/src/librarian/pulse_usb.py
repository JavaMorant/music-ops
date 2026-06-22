"""Read CDJ-stick play history and derive DJ-play analytics — combining BOTH
export formats on the stick:

  - Device Library (export.pdb)        — classic, via rekordcrate
  - Device Library Plus (exportLibrary.db) — newer SQLCipher DB (OPUS-QUAD /
    OMNIS-DUO / XDJ-AZ / CDJ-3000X), decrypted with the known fixed key.

The OPUS-QUAD only writes DL+, so for an Opus DJ that file IS the history. We
read both and merge by track (artist+title), since sets get split across them.
Histories carry play *order* but no timestamps, so per-track timing isn't
derivable — sequence-based insight (transitions, openers, genre flow) is.
"""
from __future__ import annotations

import collections
import re
import subprocess
from pathlib import Path

RC = str(Path.home() / ".cargo" / "bin" / "rekordcrate")
SQLCIPHER = "/opt/homebrew/opt/sqlcipher/bin/sqlcipher"
DLPLUS_KEY = "r8gddnr4k847830ar6cqzbkk0el6qytmb3trbbx805jm74vez64i5o8fnrqryqls"
_STR = r'"((?:[^"\\]|\\.)*)"'
_SEP = "\x1f"


def available() -> bool:
    return Path(RC).exists()


def _unesc(s):
    return s.encode().decode("unicode_escape", errors="replace") if "\\" in s else s


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def find_usbs():
    out = []
    vols = Path("/Volumes")
    if vols.exists():
        for v in vols.iterdir():
            rb = v / "PIONEER" / "rekordbox"
            if (rb / "export.pdb").exists() or (rb / "exportLibrary.db").exists():
                out.append(v)
    return out


# --- Device Library (export.pdb, via rekordcrate) -------------------------
def _pdb_sessions(pdb: Path):
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
    tr = {}
    for m in re.finditer(r"Track\(Track \{(.*?)\}\)", txt):
        b = m.group(1)
        i = re.search(r", id: TrackId\((\d+)\)", b)
        t = re.search(r"title: DeviceSQLString\(" + _STR + r"\)", b)
        a = re.search(r", artist_id: ArtistId\((\d+)\)", b)
        g = re.search(r"genre_id: GenreId\((\d+)\)", b)
        if i and t:
            ttl = _unesc(t.group(1))
            tr[int(i.group(1))] = (f"{artists.get(int(a.group(1)) if a else 0, '')} - {ttl}".strip(" -"),
                                   genres.get(int(g.group(1)) if g else 0, ""), 0.0, ttl)
    by_pl, seen = collections.defaultdict(list), set()
    for m in re.finditer(r"HistoryEntry \{ track_id: TrackId\((\d+)\), playlist_id: HistoryPlaylistId\((\d+)\), entry_index: (\d+) \}", txt):
        tid, pid, idx = int(m[1]), int(m[2]), int(m[3])
        if (pid, idx) in seen:
            continue
        seen.add((pid, idx))
        by_pl[pid].append((idx, tid))
    sessions, meta = [], {}
    for pid in sorted(by_pl):
        seq = [tr[t][0] for _, t in sorted(by_pl[pid]) if t in tr]
        for _, t in by_pl[pid]:
            if t in tr:
                meta[_norm(tr[t][0])] = {"genre": tr[t][1], "bpm": tr[t][2], "title": tr[t][3]}
        if seq:
            sessions.append({"fmt": "DL", "id": pid, "name": f"DL set #{pid}", "tracks": seq})
    return sessions, meta


# --- Device Library Plus (exportLibrary.db, SQLCipher) --------------------
def dlplus_available() -> bool:
    return Path(SQLCIPHER).exists()


def _sqlcipher(db_path: Path, sql: str):
    inp = (f"PRAGMA key='{DLPLUS_KEY}';\nPRAGMA cipher_compatibility=4;\n"
           f".mode list\n.separator '{_SEP}'\n{sql}\n")
    out = subprocess.run([SQLCIPHER, str(db_path)], input=inp,
                         capture_output=True, text=True, timeout=180).stdout
    rows = []
    for ln in out.splitlines():
        if ln == "ok" or not ln:
            continue
        rows.append(ln.split(_SEP))
    return rows


def _dlplus_sessions(db_path: Path):
    rows = _sqlcipher(db_path,
        "SELECT hc.history_id, c.title, COALESCE(a.name,''), COALESCE(g.name,''), c.bpmx100 "
        "FROM history_content hc JOIN content c ON c.content_id=hc.content_id "
        "LEFT JOIN artist a ON a.artist_id=c.artist_id_artist "
        "LEFT JOIN genre g ON g.genre_id=c.genre_id ORDER BY hc.history_id, hc.sequenceNo;")
    names = {r[0]: r[1] for r in _sqlcipher(db_path, "SELECT history_id, name FROM history;") if len(r) >= 2}
    by_hist, meta = collections.defaultdict(list), {}
    for r in rows:
        if len(r) < 5:
            continue
        hid, title, artist, genre, bpm = r
        label = f"{artist} - {title}".strip(" -")
        by_hist[hid].append(label)
        meta[_norm(label)] = {"genre": genre, "bpm": (int(bpm) / 100 if bpm.strip().isdigit() else 0.0), "title": title}
    return [{"fmt": "DL+", "id": int(h), "name": names.get(h, f"DL+ set #{h}"), "tracks": by_hist[h]}
            for h in sorted(by_hist, key=int)], meta


def read_stick(vol: Path):
    """Combined sessions (DL first/older, DL+ last/newer) + per-track meta."""
    rb = vol / "PIONEER" / "rekordbox"
    sessions, meta = [], {}
    fmts = []
    if (rb / "export.pdb").exists():
        s, m = _pdb_sessions(rb / "export.pdb")
        sessions += s
        meta.update(m)
        fmts.append("DL")
    if (rb / "exportLibrary.db").exists() and dlplus_available():
        s, m = _dlplus_sessions(rb / "exportLibrary.db")
        sessions += s
        meta.update(m)
        fmts.append("DL+")
    return sessions, meta, fmts


def _loaded_count(vol: Path) -> int:
    """Tracks loaded on the stick (not the play history)."""
    rb = vol / "PIONEER" / "rekordbox"
    if (rb / "exportLibrary.db").exists() and dlplus_available():
        try:
            r = _sqlcipher(rb / "exportLibrary.db", "SELECT count(*) FROM content;")
            if r and r[0] and r[0][0].strip().isdigit():
                return int(r[0][0])
        except Exception:
            pass
    return 0


def usb_insights(vol: Path, last_n: int = 50, source: str = "all") -> dict:
    sess, meta, fmts = read_stick(vol)
    if source in ("DL", "DL+"):
        sess = [s for s in sess if s["fmt"] == source]
    sessions = [s["tracks"] for s in sess]  # to label-lists for the analytics
    recent = sessions[-last_n:] if last_n else sessions
    recent_ids = {_norm(t) for s in recent for t in s}
    plays = collections.Counter(_norm(t) for s in sessions for t in s)
    recent_plays = collections.Counter(_norm(t) for s in recent for t in s)
    disp = {}
    for s in sessions:
        for t in s:
            disp.setdefault(_norm(t), t)

    def lbl(k):
        return disp.get(k, k)

    trans = collections.Counter()
    genre_flow = collections.Counter()
    for s in recent:
        ks = [_norm(t) for t in s]
        for a, b in zip(ks, ks[1:]):
            if a != b:
                trans[(a, b)] += 1
            ga = meta.get(a, {}).get("genre", "")
            gb = meta.get(b, {}).get("genre", "")
            if ga and gb and ga != gb:
                genre_flow[(ga, gb)] += 1

    openers = collections.Counter(_norm(s[0]) for s in recent if s)
    closers = collections.Counter(_norm(s[-1]) for s in recent if s)
    cold = [{"label": lbl(k), "plays": plays[k]} for k, _ in plays.most_common() if k not in recent_ids][:12]
    gl = collections.Counter(meta.get(k, {}).get("genre", "") for k in recent_ids if meta.get(k, {}).get("genre"))
    lengths = [len(s) for s in recent if s]
    bpms = [meta[k]["bpm"] for k in recent_ids if meta.get(k, {}).get("bpm")]

    return {
        "name": vol.name,
        "formats": fmts,
        "source": source,
        "loaded": _loaded_count(vol),
        "tracks": len(plays),
        "plays": sum(plays.values()),
        "sessions": len(sessions),
        "window": len(recent),
        "avg_per_set": round(sum(lengths) / max(1, len(lengths)), 1),
        "set_lengths": {"min": min(lengths) if lengths else 0, "max": max(lengths) if lengths else 0},
        "bpm_range": ({"low": round(min(bpms)), "high": round(max(bpms))} if bpms else None),
        "distinct_played": len(plays),
        "coverage_note": f"{len(plays)} distinct tracks played across {len(sessions)} sets",
        "hot": [{"label": lbl(k), "plays": n} for k, n in recent_plays.most_common(15)],
        "cold": cold,
        "transitions": [{"from": lbl(a), "to": lbl(b), "count": n}
                        for (a, b), n in trans.most_common(8) if n > 1],
        "genre_flow": [{"from": a, "to": b, "count": n} for (a, b), n in genre_flow.most_common(8)],
        "openers": [{"label": lbl(k), "count": n} for k, n in openers.most_common(6)],
        "closers": [{"label": lbl(k), "count": n} for k, n in closers.most_common(6)],
        "genre_lean": [{"genre": g, "count": c} for g, c in gl.most_common(8)],
    }


def stick_sets(vol: Path, *, limit: int = 60) -> list[dict]:
    """Most-recent sets on a stick, each with its tracklist in play order and a
    stable key (for naming / linking a recording in a sidecar)."""
    sess, _meta, _fmts = read_stick(vol)
    out = []
    for s in reversed(sess):  # newest first
        out.append({
            "key": f"{vol.name}|{s['fmt']}|{s['id']}",
            "fmt": s["fmt"], "auto_name": s["name"],
            "n": len(s["tracks"]), "tracks": s["tracks"],
        })
        if len(out) >= limit:
            break
    return out
