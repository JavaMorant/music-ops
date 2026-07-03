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
import json
import re
import shutil
import zipfile
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
    artists: str          # collaborators ("" if none)
    suitable_for: str = ""  # rapper/artist names this beat suits ("" if none)
    notes: str = ""         # free-text note about the beat ("" if none)


@dataclass
class PackMeta:
    name: str           # pack name, e.g. "Trap Pack — June"
    producer: str       # your name/alias, stamped on the pack
    made_on: str        # ISO date string (caller stamps; keeps this pure)
    contact: str = ""   # optional email/handle


_SEP = re.compile(r"[\\/\r\n\x00-\x1f]+")
_TIDY = re.compile(r"\s+")
_EMAIL = re.compile(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+")


def _email_in(s: str | None) -> str:
    """Pull an email address out of a free-text contact line ("" if none)."""
    m = _EMAIL.search(s or "")
    return m.group(0) if m else ""


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
        if t.suitable_for:
            lines.append(f"      suitable for: {t.suitable_for}")
        if t.notes:
            lines.append(f"      note: {t.notes}")
    if meta.contact:
        lines += ["", f"Contact: {meta.contact}"]
    return "\n".join(lines) + "\n"


# Shared turntable visualizer + beat-reactive effects. The engine lives in
# web/deck/turntable.js (single source of truth); it's loaded here so the
# exported pack inlines it and the web app serves it at
# /api/turntable.js. ``ttRun(opts)`` runs one rAF loop that draws a glowing,
# frequency-coloured mirrored spectrum + a progress ring + a bass-driven halo and
# sparks, and (in reel mode) pulses the label, breathes, and shakes the scene to
# the kick. opts = {canvas, audio, getAnalyser, scene, label, reelGet, sparksOn}.
_WEB_DIR = Path(__file__).parent / "web"
TURNTABLE_JS = (_WEB_DIR / "deck" / "turntable.js").read_text(encoding="utf-8")
DECK_JS = (_WEB_DIR / "deck" / "deck.js").read_text(encoding="utf-8")
# Theme tokens inlined into the pack <style> block — both [data-theme] blocks from
# themes.css, minus the @font-face/@font-display declarations so the zip stays
# font-file-free (Fraunces falls back to Georgia; the rest of the palette is intact).
THEME_TOKENS = "\n".join(
    l for l in (_WEB_DIR / "css" / "themes.css").read_text(encoding="utf-8").splitlines()
    if "@font-face" not in l and ".woff2" not in l
)


_PLAYER_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" data-theme="__THEME__"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>__PACK_NAME__</title>
<style>
  :root{--deck-size:330px;}
  __THEME_TOKENS__
  *{box-sizing:border-box;}
  body{margin:0;background:radial-gradient(1100px 560px at 50% -8%,#1c1c24,var(--bg));color:var(--txt);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
  .wrap{max-width:540px;margin:0 auto;padding:32px 20px 80px;text-align:center;will-change:transform;backface-visibility:hidden;}
  h1{font-size:24px;letter-spacing:.02em;margin:0 0 4px;}
  .by{color:var(--dim);margin:0 0 18px;letter-spacing:.03em;}
  .by b{color:var(--accent);font-weight:600;}
  .reelbtn{position:fixed;top:12px;right:12px;z-index:9;background:#20202a;color:var(--txt);
    border:1px solid var(--line);border-radius:20px;padding:7px 14px;font-size:13px;cursor:pointer;}
  .reelbtn:hover{border-color:var(--accent);}
  .fxbar{position:fixed;top:12px;left:12px;z-index:9;display:flex;gap:6px;flex-wrap:wrap;max-width:62vw;}
  .fxchip{background:#20202a;color:var(--accent);border:1px solid var(--accent);border-radius:20px;
    padding:6px 12px;font-size:12px;cursor:pointer;}
  .fxchip.off{color:var(--dim);border-color:var(--line);}
__DECK_CSS__
  /* 9:16 reel mode: scale the shared deck; the rest of reel layout is below */
  body.reel .deck{width:min(82vw,58vh);height:min(82vw,58vh);margin:0 auto 26px;}
  .fx{position:fixed;inset:0;pointer-events:none;z-index:5;}  /* grain only; vignette is the toggleable .fx-vig layer */
  .fx::after{content:"";position:absolute;inset:0;opacity:.045;mix-blend-mode:overlay;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");}
  .fx-vig,.fx-vhs{position:fixed;inset:0;z-index:6;}
  .pfx{position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:6;}
  .flash{position:fixed;inset:0;pointer-events:none;z-index:7;opacity:0;}
  .hook{font-weight:800;font-size:19px;letter-spacing:.04em;text-transform:uppercase;color:var(--accent);
    min-height:1.3em;margin:0 0 10px;text-shadow:0 0 14px rgba(201,162,39,.4);}
  @keyframes hookpop{0%{opacity:0;transform:scale(.7) translateY(8px);}60%{opacity:1;transform:scale(1.06);}100%{opacity:1;transform:none;}}
  .hook.pop{animation:hookpop .5s cubic-bezier(.2,.9,.3,1.2);}
  body.reel .hook{font-size:24px;}
  .now{display:flex;align-items:center;gap:14px;justify-content:center;margin-bottom:22px;}
  .play{width:54px;height:54px;border-radius:50%;border:none;background:var(--accent);color:#10100a;
    font-size:19px;cursor:pointer;flex:none;box-shadow:0 6px 18px rgba(201,162,39,.3);}
  .nowinfo{text-align:left;min-width:160px;max-width:320px;}
  .nt{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .nm{color:var(--dim);font-size:13px;}
  @keyframes nfin{from{opacity:0;transform:translateY(7px);}to{opacity:1;transform:none;}}
  .nfin{animation:nfin .38s cubic-bezier(.2,.8,.2,1);}
  .bpmdot{display:none;width:9px;height:9px;border-radius:50%;background:var(--accent);flex:none;
    box-shadow:0 0 8px var(--accent);animation:blink 1s ease-in-out infinite;}
  @keyframes blink{0%,100%{opacity:.2;transform:scale(.8);}45%{opacity:1;transform:scale(1.15);}}
  ol.list{list-style:none;margin:0;padding:0;text-align:left;}
  ol.list li{display:flex;gap:11px;align-items:center;padding:11px 13px;border:1px solid var(--line);
    border-radius:9px;margin-bottom:9px;cursor:pointer;background:var(--panel);}
  ol.list li:hover{border-color:#4a4a55;}
  ol.list li.active{border-color:var(--accent);background:#1d1d12;}
  ol.list .num{color:var(--accent);font-weight:600;font-variant-numeric:tabular-nums;}
  ol.list .til{display:flex;flex-direction:column;gap:2px;min-width:0;}
  ol.list .ti{font-weight:500;}
  ol.list .sf{color:var(--accent);font-size:11px;font-weight:600;letter-spacing:.02em;}
  ol.list .sf:empty{display:none;}
  ol.list .nt2{color:var(--dim);font-size:11px;line-height:1.35;
    display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
  ol.list .nt2:empty{display:none;}
  ol.list .me{color:var(--dim);font-size:13px;margin-left:auto;white-space:nowrap;}
  ol.list .inq{margin-left:10px;background:none;border:1px solid var(--line);color:var(--dim);
    border-radius:7px;padding:4px 10px;font-size:12px;cursor:pointer;flex:none;}
  ol.list .inq:hover{border-color:var(--accent);color:var(--accent);}
  .contact{color:var(--dim);margin-top:24px;}
  footer{color:var(--dim);font-size:12px;margin-top:30px;}
  /* 9:16 reel mode — fill the frame for a vertical screen-record */
  body.reel .wrap{max-width:none;min-height:100vh;display:flex;flex-direction:column;
    align-items:center;justify-content:center;padding:16px;}
  body.reel h1,body.reel .by,body.reel ol.list,body.reel footer,body.reel .contact{display:none;}
  body.reel .deck{width:min(82vw,58vh);height:min(82vw,58vh);margin:0 auto 26px;}
  body.reel .now{transform:scale(1.15);margin-bottom:0;}
</style>
<script>var _t=new URLSearchParams(location.search).get("theme");if(_t)document.documentElement.dataset.theme=_t;</script>
</head>
<body>
<button class="reelbtn" id="skinbtn" style="right:96px">Cassette</button>
<button class="reelbtn" id="reelbtn">⤢ Reel</button>
<div class="fxbar" id="fxbar">
  <button class="fxchip" data-fx="smoke">Smoke</button>
  <button class="fxchip" data-fx="particles">Particles</button>
  <button class="fxchip" data-fx="shake">Shake</button>
  <button class="fxchip" data-fx="heat">Heat</button>
  <button class="fxchip" data-fx="vhs">VHS</button><button class="fxchip" data-fx="vignette">Vignette</button><button class="fxchip" data-fx="dust">Dust</button>
</div>
<div class="wrap">
  <h1>__PACK_NAME__</h1>
  <p class="by">Produced by <b>__PRODUCER__</b> · __MADE__ · __NBEATS__ beats</p>
  <div class="hook" id="hook"></div>
  __DECK_HTML__
  <div class="now">
    <button class="play" id="play">&#9654;</button>
    <span class="bpmdot" id="bpmdot"></span>
    <div class="nowinfo"><div class="nt" id="nt">Tap a beat ↓</div><div class="nm" id="nm"></div></div>
  </div>
  <ol class="list" id="list"></ol>
  __CONTACT__
  <footer>Made with releases · serve this folder or drop it on a static host to share</footer>
</div>
<canvas class="pfx" id="pfx"></canvas>
<div class="fx-vig"></div><div class="fx-vhs"></div>
<div class="fx"></div>
<div class="flash"></div>
<audio id="audio"></audio>
<script>
__DECK_JS__
__VIZ_JS__
const PACK_COVER = __COVER_URL__;
const TRACKS = __TRACKS__;
const INQ = __INQUIRE_JSON__;  // {contact,producer,pack} — drives the per-beat Inquire button
function inquire(i){var t=TRACKS[i];  // mailto for now; the hosted build POSTs this to a tracked endpoint
  var subj='Beat inquiry: '+t.t+(INQ.pack?' ('+INQ.pack+')':'');
  var body='Hi'+(INQ.producer?' '+INQ.producer:'')+',\n\nI'm interested in "'+t.t+'"'+(INQ.pack?' from your '+INQ.pack+' pack':'')+'. Is it available?\n\n';
  location.href='mailto:'+INQ.contact+'?subject='+encodeURIComponent(subj)+'&body='+encodeURIComponent(body);}
const audio=document.getElementById('audio'),vinyl=document.getElementById('vinyl'),arm=document.getElementById('arm');
const playBtn=document.getElementById('play'),nt=document.getElementById('nt'),nm=document.getElementById('nm'),list=document.getElementById('list');
let cur=-1,actx,analyser,data;
TRACKS.forEach((t,i)=>{const li=document.createElement('li');
  li.innerHTML='<span class="num"></span><span class="til"><span class="ti"></span><span class="sf"></span><span class="nt2"></span></span><span class="me"></span>';
  li.querySelector('.num').textContent=String(i+1).padStart(2,'0');
  li.querySelector('.ti').textContent=t.t; li.querySelector('.me').textContent=t.m;
  if(t.sf)li.querySelector('.sf').textContent='▸ for '+t.sf;
  if(t.n)li.querySelector('.nt2').textContent=t.n;
  if(INQ.contact){var ib=document.createElement('button');ib.className='inq';ib.textContent='Inquire';
    ib.title='Email '+(INQ.producer||'the producer')+' about this beat';
    ib.onclick=function(e){e.stopPropagation();inquire(i);};li.appendChild(ib);}
  li.onclick=()=>select(i); list.appendChild(li);});
function select(i){cur=i;const t=TRACKS[i];audio.src=encodeURI(t.f);
  nt.textContent=t.t;nm.textContent=t.m;
  const nfo=document.querySelector('.nowinfo');nfo.classList.remove('nfin');void nfo.offsetWidth;nfo.classList.add('nfin');
  const dot=document.getElementById('bpmdot');if(t.b){dot.style.display='inline-block';dot.style.animationDuration=(60/t.b).toFixed(3)+'s';}else{dot.style.display='none';}
  const hook=document.getElementById('hook');hook.textContent=[(t.g&&t.g!=='unknown')?t.g.toUpperCase():'',t.b?t.b+' BPM':''].filter(Boolean).join(' · ');
  hook.classList.remove('pop');void hook.offsetWidth;hook.classList.add('pop');
  [...list.children].forEach((li,j)=>li.classList.toggle('active',j===i));
  audio.play().catch(()=>{});}
function setPlaying(p){deckSetPlaying(document.body,p);playBtn.innerHTML=p?'&#10074;&#10074;':'&#9654;';}
document.getElementById('skinbtn').onclick=function(){var on=!document.body.classList.contains('cassette-mode');
  deckSetSkin(document.body,on?'cassette':'vinyl');this.textContent=on?'Vinyl':'Cassette';size();};
audio.addEventListener('play',()=>{initViz();setPlaying(true);});
audio.addEventListener('pause',()=>setPlaying(false));
audio.addEventListener('ended',()=>{cur<TRACKS.length-1?select(cur+1):setPlaying(false);});
function togglePlay(){if(cur<0){select(0);return;}audio.paused?audio.play():audio.pause();}
playBtn.onclick=togglePlay;
ttScrub({vinyl:vinyl,audio:audio,secPerRev:4,onTap:togglePlay});
const reelbtn=document.getElementById('reelbtn');
reelbtn.onclick=()=>{const on=document.body.classList.toggle('reel');
  reelbtn.textContent=on?'✕ Exit':'⤢ Reel';size();};
// FX toggles — each chip flips an off-<name> class on <body>; all on by default
document.querySelectorAll('.fxchip').forEach(function(ch){
  ch.onclick=function(){ch.classList.toggle('off',document.body.classList.toggle('off-'+ch.dataset.fx));};});
const canvas=document.getElementById('viz');
function size(){const s=document.getElementById('deck').clientWidth;canvas.width=s*2;canvas.height=s*2;}
size();addEventListener('resize',size);
function initViz(){
  if(actx){if(actx.state==='suspended')actx.resume();return;}
  try{actx=new (window.AudioContext||window.webkitAudioContext)();
    const src=actx.createMediaElementSource(audio);
    analyser=actx.createAnalyser();analyser.fftSize=256;analyser.smoothingTimeConstant=.6;
    src.connect(analyser);analyser.connect(actx.destination);
  }catch(e){/* file:// or unsupported — vinyl still spins, audio still plays */}
}
ttRun({canvas:canvas,audio:audio,getAnalyser:function(){return analyser;},
  fxCanvas:document.getElementById('pfx'),smokeAt:function(){return deckSmokeAt(document.body);},
  scene:document.querySelector('.wrap'),
  getLabel:function(){return document.body.classList.contains('cassette-mode')?document.querySelector('#cassette .clabel'):document.querySelector('#vinyl .label');},
  flash:document.querySelector('.flash'),getCover:function(){return PACK_COVER;},
  getSkin:function(){return document.body.classList.contains('cassette-mode')?'cassette':'vinyl';},
  fxOn:function(n){return !document.body.classList.contains('off-'+n);},
  reelGet:function(){return document.body.classList.contains('reel');},sparksOn:true});
</script>
</body></html>
"""


def render_index_html(
    tracks: list[PackTrack], meta: PackMeta, filenames: list[str], cover: str | None = None,
    theme: str = "editorial",
) -> str:
    items = [{"t": t.title, "m": _meta_str(t), "f": fn, "b": t.bpm or 0, "g": t.genre,
              "sf": t.suitable_for, "n": t.notes}
             for t, fn in zip(tracks, filenames)]
    # JSON for the <script> context: escape <, >, & so a title can't break out of it.
    tracks_json = (
        json.dumps(items, ensure_ascii=False)
        .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )
    if cover:
        # cover is a pack-relative filename we control (e.g. "cover.jpg"); show it
        # as the vinyl/cassette label (picture-disc style) instead of producer text.
        c = html.escape(cover, quote=True)
        label_html = f'<div class="label cover" style="background-image:url(\'{c}\')"></div>'
        clabel_html = f'<div class="clabel cover" style="background-image:url(\'{c}\')"></div>'
    else:
        text = html.escape((meta.producer or "").strip()[:16] or "Beats")
        label_html = f'<div class="label">{text}</div>'
        clabel_html = f'<div class="clabel">{text}</div>'
    contact = f'<p class="contact">{html.escape(meta.contact)}</p>' if meta.contact else ""
    # per-beat "Inquire" button → a pre-filled mailto to the producer (when the
    # contact line carries an email). The hosted build will swap this one call for
    # a tracked POST; everything else stays the same.
    inq = json.dumps(
        {"contact": _email_in(meta.contact), "producer": meta.producer, "pack": meta.name},
        ensure_ascii=False,
    ).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    subs = {
        "__PACK_NAME__": html.escape(meta.name),
        "__PRODUCER__": html.escape(meta.producer),
        "__MADE__": html.escape(meta.made_on),
        "__NBEATS__": str(len(tracks)),
        "__LABEL_HTML__": label_html,
        "__CLABEL_HTML__": clabel_html,
        "__CONTACT__": contact,
        "__VIZ_JS__": TURNTABLE_JS,
        "__COVER_URL__": json.dumps(cover),
        "__TRACKS__": tracks_json,
        "__INQUIRE_JSON__": inq,
        "__THEME__": html.escape(theme),
    }
    out = _PLAYER_TEMPLATE
    out = out.replace("__DECK_CSS__", (_WEB_DIR / "deck" / "deck.css").read_text(encoding="utf-8"))
    out = out.replace("__DECK_HTML__", (_WEB_DIR / "deck" / "deck.html").read_text(encoding="utf-8"))
    out = out.replace("__DECK_JS__", DECK_JS)
    out = out.replace("__THEME_TOKENS__", THEME_TOKENS)
    for token, value in subs.items():
        out = out.replace(token, value)
    return out


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def build_pack(
    tracks: list[PackTrack], out_dir: Path, meta: PackMeta, cover_src: Path | None = None,
    clean: bool = False, preview: bool = False, preview_seconds: int = 40,
    theme: str = "editorial",
) -> list[str]:
    """Write the pack into ``out_dir`` (created): clean-named audio + index.html +
    tracklist.txt. ``cover_src``, if an image, is copied in as the vinyl-label cover
    art. Returns the list of audio filenames written. Read-only on the sources.

    If ``clean``, an existing ``out_dir`` is wiped first so a rebuilt pack never
    carries stale beats from a previous run (a re-zip would otherwise ship them).

    If ``preview``, each beat is exported as a protected preview (trimmed to
    ``preview_seconds`` with a quiet periodic tag tone) instead of the full file,
    so the pack can't be ripped. Requires ffmpeg (raises PreviewError otherwise)."""
    if clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filenames: list[str] = []
    used: dict[str, int] = {}
    for i, t in enumerate(tracks, 1):
        fn = clean_track_filename(i, t)
        if preview:
            fn = str(Path(fn).with_suffix(".mp3"))  # previews are re-encoded to mp3
        if fn in used:
            used[fn] += 1
            s = Path(fn)
            fn = f"{s.stem} ({used[fn]}){s.suffix}"
        else:
            used[fn] = 0
        if preview:
            from . import preview as preview_mod
            preview_mod.make_preview(t.src, out_dir / fn, seconds=preview_seconds)
        else:
            shutil.copy2(t.src, out_dir / fn)
        filenames.append(fn)
    cover_name = None
    if cover_src is not None and cover_src.is_file() and is_image(cover_src):
        cover_name = "cover" + cover_src.suffix.lower()
        shutil.copy2(cover_src, out_dir / cover_name)
    (out_dir / "index.html").write_text(
        render_index_html(tracks, meta, filenames, cover=cover_name, theme=theme), encoding="utf-8"
    )
    (out_dir / "tracklist.txt").write_text(render_tracklist_txt(tracks, meta), encoding="utf-8")
    return filenames


def zip_pack(folder: Path, zip_path: Path) -> Path:
    """Zip a built pack ``folder`` into ``zip_path`` with the folder itself as the
    single top-level entry, so it extracts cleanly to one named folder. Stored
    (no deflate) since the audio is already compressed. Returns ``zip_path``.
    Arc names are sanitised so a file named with a separator can't escape on
    extraction (zip-slip)."""
    top = _SEP.sub("_", folder.name) or "pack"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                rel = "/".join(_SEP.sub("_", part) for part in f.relative_to(folder).parts)
                z.write(f, arcname=f"{top}/{rel}")
    return zip_path
