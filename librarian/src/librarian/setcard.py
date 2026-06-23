"""Render one DJ set as a self-contained, shareable HTML card.

No backend, no API keys: every track links out to a Spotify / SoundCloud /
Bandcamp *search* for its artist+title, so whoever you send it to can pull the
song up on whatever platform they use. The output is one standalone HTML
document — open it in a browser, AirDrop it, or drop it on the EPK site.
"""
from __future__ import annotations

import html
import urllib.parse

# Platform brand colours for the per-track link pills.
_PLAT = {
    "Spotify": ("#1db954", "https://open.spotify.com/search/{q}"),
    "SoundCloud": ("#ff5500", "https://soundcloud.com/search?q={q}"),
    "Bandcamp": ("#629aa9", "https://bandcamp.com/search?q={q}"),
}

_CSS = (
    "*{box-sizing:border-box}"
    "body{margin:0;background:#0e0f12;color:#e7e9ee;padding:30px 16px;"
    "font:15px/1.5 ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif}"
    ".card{max-width:660px;margin:0 auto;background:#16181d;border:1px solid #262a31;"
    "border-radius:16px;padding:28px 26px}"
    ".eyebrow{color:#7aa2ff;font-size:11px;letter-spacing:.18em;font-weight:700}"
    "h1{margin:8px 0 4px;font-size:27px;letter-spacing:-.01em;line-height:1.15}"
    ".sub{color:#8b909b;font-size:13px}"
    ".chips{display:flex;gap:6px;flex-wrap:wrap;margin:14px 0 2px}"
    ".chip{font-size:11px;color:#8b909b;border:1px solid #262a31;border-radius:99px;padding:2px 9px}"
    ".rec{display:inline-block;margin-top:14px;color:#5fd08a;text-decoration:none;font-size:13px;"
    "font-weight:600;border:1px solid #5fd08a55;border-radius:8px;padding:6px 12px}"
    "ol.tl{list-style:none;margin:20px 0 0;padding:0}"
    "ol.tl li{display:flex;gap:10px;align-items:center;padding:9px 0;border-bottom:1px solid #1c1f25}"
    ".rk{color:#454b57;width:22px;text-align:right;font-size:12px;font-variant-numeric:tabular-nums}"
    ".nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
    ".nm .ar{color:#8b909b}"
    ".bpm{color:#8b909b;font-size:11px;font-variant-numeric:tabular-nums}"
    ".lks{display:inline-flex;gap:5px;flex-wrap:wrap}"
    ".lk{font-size:10px;font-weight:600;text-decoration:none;padding:2px 7px;border-radius:99px;white-space:nowrap}"
    ".foot{color:#454b57;font-size:11px;margin-top:20px;text-align:center}"
)


def split_label(label: str) -> tuple[str, str]:
    """'Artist - Title' -> (artist, title). Falls back to ('', label)."""
    parts = (label or "").split(" - ", 1)
    if len(parts) == 2 and parts[0].strip():
        return parts[0].strip(), parts[1].strip()
    return "", (label or "").strip()


def streaming_links(label: str) -> dict[str, str]:
    """Search deep-links for a track on each platform (no API, no auth)."""
    artist, title = split_label(label)
    query = f"{artist} {title}".strip() or (label or "").strip()
    q = urllib.parse.quote(query, safe="")
    return {name: tmpl.format(q=q) for name, (_c, tmpl) in _PLAT.items()}


def _links_html(label: str) -> str:
    out = []
    for name, url in streaming_links(label).items():
        colour = _PLAT[name][0]
        out.append(
            f'<a class="lk" href="{html.escape(url)}" target="_blank" rel="noopener" '
            f'style="color:{colour};border:1px solid {colour}55">{name}</a>'
        )
    return '<span class="lks">' + "".join(out) + "</span>"


def _energy_arc(bpms: list) -> str:
    b = [x for x in bpms if x and x > 0]
    if len(b) < 2:
        return ""
    mn, mx = min(b), max(b)
    rng = (mx - mn) or 1
    bars = "".join(
        f'<span title="{x} BPM" style="flex:1;background:#7aa2ff;border-radius:1px;'
        f'height:{20 + 70 * (x - mn) / rng:.0f}%"></span>'
        for x in b
    )
    return (
        f'<div style="display:flex;align-items:flex-end;gap:1px;height:46px;margin:18px 0 4px">{bars}</div>'
        f'<div style="color:#8b909b;font-size:11px">⚡ energy arc · BPM {mn}–{mx} across the set</div>'
    )


def _row(i: int, t: dict) -> str:
    label = t.get("label", "")
    artist, title = split_label(label)
    bpm = t.get("bpm", 0)
    nm = (f'<span class="ar">{html.escape(artist)} — </span>' if artist else "") + html.escape(title)
    bpm_html = f'<span class="bpm">{bpm}</span>' if bpm else ""
    return (
        f'<li><span class="rk">{i}</span><span class="nm">{nm}</span>'
        f"{bpm_html}{_links_html(label)}</li>"
    )


def render_card(detail: dict, name: str, recording: str = "", stick: str = "") -> str:
    """``detail`` is a pulse_usb.set_detail() dict (tracks = [{label,bpm,genre}])."""
    tracks = detail.get("tracks", [])
    n = detail.get("n", len(tracks))
    fmt = detail.get("fmt", "")
    bpms = [t.get("bpm", 0) for t in tracks if t.get("bpm")]
    bpm_txt = f"BPM {min(bpms)}–{max(bpms)}" if bpms else ""
    sub = " · ".join(x for x in [html.escape(stick), html.escape(fmt), f"{n} tracks", bpm_txt] if x)

    genres: list[str] = []
    for t in tracks:
        g = (t.get("genre") or "").strip()
        if g and g not in genres:
            genres.append(g)
    chips = ""
    if genres:
        chips = '<div class="chips">' + "".join(
            f'<span class="chip">{html.escape(g)}</span>' for g in genres[:6]
        ) + "</div>"

    rec = ""
    r = (recording or "").strip()
    if r.startswith(("http://", "https://")):
        rec = f'<a class="rec" href="{html.escape(r)}" target="_blank" rel="noopener">▶ Listen to the recording</a>'

    rows = "".join(_row(i, t) for i, t in enumerate(tracks, 1))
    og_desc = " · ".join(x for x in [f"{n} tracks", bpm_txt, "made with pulse"] if x)

    return (
        '<!DOCTYPE html>\n<html lang="en"><head>\n<meta charset="utf-8"/>\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>\n'
        f"<title>{html.escape(name)} — set recap</title>\n"
        f'<meta property="og:title" content="{html.escape(name)}"/>\n'
        f'<meta property="og:description" content="{html.escape(og_desc)}"/>\n'
        '<meta property="og:type" content="music.playlist"/>\n'
        f"<style>{_CSS}</style>\n</head><body>\n"
        '<div class="card">\n'
        '<div class="eyebrow">PULSE · SET RECAP</div>\n'
        f"<h1>{html.escape(name)}</h1>\n"
        f'<div class="sub">{sub}</div>\n'
        f"{chips}{rec}{_energy_arc([t.get('bpm', 0) for t in tracks])}\n"
        f'<ol class="tl">{rows}</ol>\n'
        f'<div class="foot">{n} tracks · made with <b>pulse</b> — DJ library intelligence</div>\n'
        "</div>\n</body></html>\n"
    )
