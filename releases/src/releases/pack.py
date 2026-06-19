"""Build a polished, self-contained beat pack from a set of tracks.

This is an EXPORT: it copies the chosen audio into a fresh pack folder with clean
names, a branded ``index.html`` player, and a plain-text tracklist. It never
touches the music library (read-only — only ``shutil.copy2`` out of it).

Send it two ways:
  * zip the folder → WeTransfer / Drive / email (recipient gets named beats + a
    tracklist; opening index.html plays them when served), or
  * drag the folder to a free static host (e.g. Netlify Drop) → an instant
    shareable player link. That player page is the seed of the hosted product.
"""

from __future__ import annotations

import html
import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass
class PackTrack:
    src: Path           # absolute source audio path (in the library)
    title: str          # display title (the track name)
    bpm: int | None
    key: str | None
    genre: str
    artists: str        # collaborators ("" if none)


@dataclass
class PackMeta:
    name: str           # pack name, e.g. "Trap Pack — June"
    producer: str       # your name/alias, stamped on the pack
    made_on: str        # ISO date string (caller stamps; keeps this pure)
    contact: str = ""   # optional email/handle


_SEP = re.compile(r"[\\/\r\n\x00-\x1f]+")
_TIDY = re.compile(r"\s+")


def safe_filename(name: str) -> str:
    """A single safe path component — no separators or control chars."""
    return _TIDY.sub(" ", _SEP.sub(" ", name)).strip() or "track"


def clean_track_filename(index: int, t: PackTrack) -> str:
    meta = " ".join(x for x in [str(t.bpm) if t.bpm else "", t.key or ""] if x).strip()
    stem = safe_filename(t.title)
    suffix = t.src.suffix.lower()
    tag = f" [{meta}]" if meta else ""
    return f"{index:02d} - {stem}{tag}{suffix}"


def _meta_str(t: PackTrack) -> str:
    bits = []
    if t.bpm:
        bits.append(f"{t.bpm} BPM")
    if t.key:
        bits.append(t.key)
    if t.genre and t.genre != "unknown":
        bits.append(t.genre)
    if t.artists:
        bits.append(f"feat. {t.artists}")
    return " · ".join(bits)


def render_tracklist_txt(tracks: list[PackTrack], meta: PackMeta) -> str:
    lines = [meta.name, f"Produced by {meta.producer}  ·  {meta.made_on}  ·  {len(tracks)} beats", ""]
    for i, t in enumerate(tracks, 1):
        info = _meta_str(t)
        lines.append(f"{i:>2}. {t.title}" + (f"   ({info})" if info else ""))
    if meta.contact:
        lines += ["", f"Contact: {meta.contact}"]
    return "\n".join(lines) + "\n"


def render_index_html(tracks: list[PackTrack], meta: PackMeta, filenames: list[str]) -> str:
    rows = []
    for i, (t, fn) in enumerate(zip(tracks, filenames), 1):
        info = html.escape(_meta_str(t))
        rows.append(
            f'<li><div class="t"><span class="n">{i:02d}</span>'
            f'<span class="ti">{html.escape(t.title)}</span>'
            f'<span class="m">{info}</span></div>'
            f'<audio controls preload="none" src="{html.escape(fn, quote=True)}"></audio></li>'
        )
    contact = (f'<p class="contact">{html.escape(meta.contact)}</p>' if meta.contact else "")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(meta.name)}</title>
<style>
  :root {{ --bg:#0d0d10; --panel:#16161c; --line:#26262f; --txt:#e7e7ea; --dim:#8a8a96; --accent:#c9a227; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--txt);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
  .wrap {{ max-width:760px; margin:0 auto; padding:48px 22px 80px; }}
  h1 {{ font-size:26px; letter-spacing:.02em; margin:0 0 6px; }}
  .by {{ color:var(--dim); margin:0 0 30px; letter-spacing:.03em; }}
  .by b {{ color:var(--accent); font-weight:600; }}
  ul {{ list-style:none; margin:0; padding:0; }}
  li {{ background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:13px 15px; margin-bottom:12px; }}
  .t {{ display:flex; align-items:baseline; gap:10px; margin-bottom:9px; }}
  .n {{ color:var(--accent); font-variant-numeric:tabular-nums; font-weight:600; }}
  .ti {{ font-weight:600; }}
  .m {{ color:var(--dim); font-size:13px; margin-left:auto; }}
  audio {{ width:100%; height:34px; }}
  .contact {{ color:var(--dim); margin-top:32px; }}
  footer {{ color:var(--dim); font-size:12px; margin-top:40px; }}
</style></head>
<body><div class="wrap">
  <h1>{html.escape(meta.name)}</h1>
  <p class="by">Produced by <b>{html.escape(meta.producer)}</b> · {html.escape(meta.made_on)} · {len(tracks)} beats</p>
  <ul>{''.join(rows)}</ul>
  {contact}
  <footer>Tip: serve this folder (or drop it on a static host) to play in any browser.</footer>
</div></body></html>
"""


def build_pack(tracks: list[PackTrack], out_dir: Path, meta: PackMeta) -> list[str]:
    """Write the pack into ``out_dir`` (created): clean-named audio copies +
    index.html + tracklist.txt. Returns the list of audio filenames written.
    Read-only on the sources (copy only)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    filenames: list[str] = []
    used: dict[str, int] = {}
    for i, t in enumerate(tracks, 1):
        fn = clean_track_filename(i, t)
        if fn in used:
            used[fn] += 1
            s = Path(fn)
            fn = f"{s.stem} ({used[fn]}){s.suffix}"
        else:
            used[fn] = 0
        shutil.copy2(t.src, out_dir / fn)
        filenames.append(fn)
    (out_dir / "index.html").write_text(render_index_html(tracks, meta, filenames), encoding="utf-8")
    (out_dir / "tracklist.txt").write_text(render_tracklist_txt(tracks, meta), encoding="utf-8")
    return filenames
