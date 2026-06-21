"""``librarian pulse`` — library intelligence over the rekordbox collection.

Run it on demand (e.g. every couple of weeks). It reads the live rekordbox
database (master.db, via pyrekordbox) and reports what you play, what's rising,
what's gone cold, and how much of your library you actually touch — then writes
two crates:

  - ``Untouched.m3u``   — quality tracks NOT played in your last N sets
  - ``Recommended.m3u`` — an AI pick from that pile that fits your current sound

A small state snapshot is kept between runs (in the runs dir) so "new since last
run" and "rising" are real diffs, not guesses. AI recommendations degrade
gracefully to a play-history heuristic when no API key is present.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_DATE = re.compile(r"(\d{4})[-_/](\d{2})[-_/](\d{2})")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _session_date(name: str, fallback):
    m = _DATE.search(name or "")
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            pass
    return fallback


@dataclass
class Track:
    cid: int
    artist: str
    title: str
    genre: str
    path: str | None


def _load(db):
    """Pull the collection + dated HISTORY sessions out of rekordbox."""
    from pyrekordbox.db6 import tables

    tracks: dict[int, Track] = {}
    for c in db.get_content():
        art = (getattr(c, "ArtistName", None)
               or (c.Artist.Name if getattr(c, "Artist", None) else "") or "").strip()
        genre = ""
        g = getattr(c, "Genre", None)
        if g is not None:
            genre = (getattr(g, "Name", "") or "").strip()
        tracks[c.ID] = Track(c.ID, art, (getattr(c, "Title", "") or "").strip(),
                             genre, getattr(c, "FolderPath", None))

    H, SH = tables.DjmdHistory, tables.DjmdSongHistory
    by_hist: dict[int, list[int]] = defaultdict(list)
    for hid, cid in db.session.query(SH.HistoryID, SH.ContentID).all():
        by_hist[hid].append(cid)
    sessions = []  # (date, name, [content_ids])
    for h in db.session.query(H).all():
        name = h.Name or ""
        if not name.upper().startswith("HISTORY") or h.ID not in by_hist:
            continue
        sessions.append((_session_date(name, getattr(h, "created_at", None) or datetime.min),
                         name, by_hist[h.ID]))
    sessions.sort(key=lambda s: s[0], reverse=True)
    return tracks, sessions


def _ai_recommend(candidates, recent, n, model):
    """Pick the n best resurfacing tracks from candidates that fit `recent`."""
    import os
    if not os.environ.get("ANTHROPIC_API_KEY") or not candidates:
        return []
    try:
        import anthropic
    except ImportError:
        return []
    schema = {"type": "object", "properties": {"picks": {"type": "array", "items": {
        "type": "object", "properties": {"index": {"type": "integer"}, "why": {"type": "string"}},
        "required": ["index", "why"], "additionalProperties": False}}},
        "required": ["picks"], "additionalProperties": False}
    sample = candidates[:120]
    lines = [f"You DJ sets heavy on: {', '.join(recent[:25])}.", "",
             f"From these untouched tracks, pick the {n} most worth resurfacing next set "
             "(fit the sound, recognisable, varied). One short reason each.", ""]
    for i, t in enumerate(sample):
        lines.append(f"{i}: {t.artist} - {t.title}  [{t.genre}]")
    try:
        client = anthropic.Anthropic()
        r = client.messages.create(model=model, max_tokens=2048,
                                   messages=[{"role": "user", "content": "\n".join(lines)}],
                                   output_config={"format": {"type": "json_schema", "schema": schema}})
        data = json.loads("".join(b.text for b in r.content if getattr(b, "type", None) == "text"))
        return [(sample[p["index"]], p.get("why", "")) for p in data["picks"]
                if 0 <= p["index"] < len(sample)]
    except Exception:
        return []


def run_pulse(db, *, runs_dir: Path, out_dir: Path, last_n: int = 15,
              recommend: int = 10, model: str = "claude-sonnet-4-6") -> Path:
    tracks, sessions = _load(db)
    spins = Counter()
    sess_of = defaultdict(set)
    for date, name, cids in sessions:
        for cid in cids:
            spins[cid] += 1
            sess_of[cid].add(name)
    spins_by_sessions = {cid: len(s) for cid, s in sess_of.items()}

    window = sessions[:last_n]
    window_cids = {cid for _, _, cids in window for cid in cids}
    ever = set(spins)
    untouched = [t for cid, t in tracks.items() if cid not in window_cids]
    untouched_quality = [t for t in untouched if t.path]

    # genre lean of the recent window
    recent_genres = Counter(tracks[cid].genre for _, _, cids in window
                            for cid in cids if cid in tracks and tracks[cid].genre)
    staples = sorted(spins_by_sessions.items(), key=lambda kv: -kv[1])[:15]
    cold = [(cid, n) for cid, n in sorted(spins_by_sessions.items(), key=lambda kv: -kv[1])
            if cid not in window_cids][:15]

    # diff vs last snapshot
    state_file = runs_dir / "pulse-state.json"
    prior = json.loads(state_file.read_text()) if state_file.exists() else {}
    prior_lib = set(prior.get("lib", []))
    prior_spins = prior.get("spins", {})
    new_tracks = [t for cid, t in tracks.items() if str(cid) not in prior_lib and prior_lib]
    rising = sorted(((cid, spins[cid] - prior_spins.get(str(cid), 0)) for cid in spins),
                    key=lambda kv: -kv[1])
    rising = [(cid, d) for cid, d in rising if d > 0][:10]

    # recommendations
    recent_labels = [f"{tracks[cid].artist} - {tracks[cid].title}"
                     for _, _, cids in window[:5] for cid in cids if cid in tracks][:30]
    recs = _ai_recommend(untouched_quality, recent_labels, recommend, model)

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_m3u(out_dir / "Untouched.m3u", [t.path for t in untouched_quality if t.path])
    if recs:
        _write_m3u(out_dir / "Recommended.m3u", [t.path for t, _ in recs if t.path])

    report = out_dir / "pulse-report.md"
    _write_report(report, tracks, sessions, window, untouched, untouched_quality, ever,
                  staples, cold, recent_genres, new_tracks, rising, recs, last_n)

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({
        "date": (sessions[0][0].isoformat() if sessions else ""),
        "lib": [str(c) for c in tracks], "spins": {str(c): n for c, n in spins.items()}}))
    return report


def _write_m3u(path: Path, paths) -> None:
    real = [p for p in paths if p]
    path.write_text("#EXTM3U\n" + "\n".join(sorted(set(real))) + "\n", encoding="utf-8")


def _write_report(path, tracks, sessions, window, untouched, untouched_q, ever, staples, cold,
                  recent_genres, new_tracks, rising, recs, last_n):
    def lbl(cid):
        t = tracks.get(cid)
        return f"{t.artist} - {t.title}" if t else f"<{cid}>"

    n = len(tracks)
    L = ["# Library Pulse", ""]
    L.append(f"- **Library:** {n} tracks · **{len(sessions)}** logged sets")
    if sessions:
        L.append(f"- **Window:** last {len(window)} sets "
                 f"({window[-1][0].date()} → {window[0][0].date()})")
    L.append(f"- **Coverage:** {len(ever)} tracks ever played "
             f"({100 * len(ever) // max(1, n)}%) · **{len(untouched)} untouched** in last {last_n} sets")
    if new_tracks:
        L.append(f"- **New since last run:** {len(new_tracks)} tracks added")
    L += ["", "## 🔥 Your staples (most sets)", ""]
    L += [f"{i+1}. {lbl(cid)} — {nn} sets" for i, (cid, nn) in enumerate(staples)]
    if rising:
        L += ["", "## 📈 Rising (since last run)", ""]
        L += [f"- {lbl(cid)} (+{d})" for cid, d in rising]
    L += ["", "## ❄️ Going cold (staples not in your last sets)", ""]
    L += [f"- {lbl(cid)} ({nn} sets, none lately)" for cid, nn in cold]
    if recent_genres:
        L += ["", "## 🎚️ Recent set lean", ""]
        L += [f"- {g}: {c}" for g, c in recent_genres.most_common(8)]
    if recs:
        L += ["", "## 🎯 Recommended to resurface", ""]
        L += [f"- **{t.artist} - {t.title}** — {why}" for t, why in recs]
    L += ["", "---", f"Crates written next to this report: `Untouched.m3u` "
          f"({len(untouched_q)}), " + ("`Recommended.m3u`" if recs else "(no AI recs)"), ""]
    path.write_text("\n".join(L), encoding="utf-8")


def pulse_json(db, *, last_n: int = 15) -> dict:
    """The same insights as run_pulse, returned as a JSON-serialisable dict for
    the web GUI (writes nothing)."""
    tracks, sessions = _load(db)
    sess_of = defaultdict(set)
    spins = Counter()
    for _, name, cids in sessions:
        for cid in cids:
            spins[cid] += 1
            sess_of[cid].add(name)
    by_sessions = {cid: len(s) for cid, s in sess_of.items()}
    window = sessions[:last_n]
    window_cids = {cid for _, _, cids in window for cid in cids}
    ever = set(spins)
    untouched = [cid for cid in tracks if cid not in window_cids]

    def lbl(cid):
        t = tracks.get(cid)
        return f"{t.artist} - {t.title}".strip(" -") if t else str(cid)

    ranked = sorted(by_sessions.items(), key=lambda kv: -kv[1])
    cold = [{"label": lbl(c), "sets": n} for c, n in ranked if c not in window_cids][:15]
    genres = Counter(tracks[c].genre for _, _, cids in window for c in cids
                     if c in tracks and tracks[c].genre)
    return {
        "library": len(tracks), "sessions": len(sessions), "window": len(window),
        "window_from": window[-1][0].date().isoformat() if window else None,
        "window_to": window[0][0].date().isoformat() if window else None,
        "ever_played": len(ever), "untouched": len(untouched),
        "coverage_pct": round(100 * len(ever) / max(1, len(tracks))),
        "staples": [{"label": lbl(c), "sets": n} for c, n in ranked[:20]],
        "cold": cold,
        "genre_lean": [{"genre": g, "count": c} for g, c in genres.most_common(8)],
    }
