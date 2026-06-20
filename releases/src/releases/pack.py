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


# Shared turntable visualizer + beat-reactive effects. Lives here as the single
# source of truth: the exported pack inlines it; the web app serves it at
# /api/turntable.js. ``ttRun(opts)`` runs one rAF loop that draws a glowing,
# frequency-coloured mirrored spectrum + a progress ring + a bass-driven halo and
# sparks, and (in reel mode) pulses the label, breathes, and shakes the scene to
# the kick. opts = {canvas, audio, getAnalyser, scene, label, reelGet, sparksOn}.
TURNTABLE_JS = r"""
function ttRun(opts){
  var ctx=opts.canvas.getContext('2d'), bassAvg=0, shake=0, sparks=[], dataArr=null;
  function frame(){
    requestAnimationFrame(frame);
    var W=opts.canvas.width; if(!W){return;}
    var an=opts.getAnalyser(), bass=0, energy=0;
    if(an){ if(!dataArr||dataArr.length!==an.frequencyBinCount){dataArr=new Uint8Array(an.frequencyBinCount);}
      an.getByteFrequencyData(dataArr);
      for(var i=0;i<5;i++){bass+=dataArr[i];} bass/=1275;
      for(var j=0;j<dataArr.length;j++){energy+=dataArr[j];} energy/=dataArr.length*255;
    }
    bassAvg=bassAvg*0.9+bass*0.1;
    var kick=Math.max(0,bass-bassAvg-0.05);
    if(opts.label){opts.label.style.transform='scale('+(1+Math.min(0.22,kick*1.5)).toFixed(3)+')';}
    if(kick>0.05){shake=Math.min(9,kick*32); if(opts.sparksOn){spawn(kick);}}
    shake*=0.8;
    if(opts.scene){
      if(opts.reelGet&&opts.reelGet()){
        var sx=(Math.random()*2-1)*shake, sy=(Math.random()*2-1)*shake;
        opts.scene.style.transform='translate('+sx.toFixed(1)+'px,'+sy.toFixed(1)+'px) scale('+(1.05+Math.min(0.05,energy*0.06)).toFixed(3)+')';
      } else { opts.scene.style.transform=''; }
    }
    draw(energy);
  }
  function draw(energy){
    var W=opts.canvas.width, cx=W/2, cy=W/2, R0=W*0.36, maxOuter=W*0.475;
    ctx.clearRect(0,0,W,W);
    if(energy>0.01){var g=ctx.createRadialGradient(cx,cy,R0*0.55,cx,cy,R0*(1.1+energy*0.45));
      g.addColorStop(0,'rgba(201,162,39,'+(0.08+energy*0.25).toFixed(3)+')'); g.addColorStop(1,'rgba(201,162,39,0)');
      ctx.fillStyle=g; ctx.beginPath(); ctx.arc(cx,cy,R0*1.35,0,6.2832); ctx.fill();}
    if(dataArr){var bars=96; ctx.save(); ctx.shadowBlur=W*0.011; ctx.lineCap='round'; ctx.lineWidth=W*0.013;
      for(var i=0;i<bars;i++){var idx=i<bars/2?i:bars-1-i; var v=dataArr[Math.floor(idx/(bars/2)*dataArr.length*0.7)]/255;
        var len=Math.min(W*0.012+v*v*W*0.11, maxOuter-R0), a=i/bars*6.2832-1.5708, c=Math.cos(a), s=Math.sin(a);
        var col='hsl('+(32+idx/(bars/2)*26).toFixed(0)+','+(62+v*30).toFixed(0)+'%,'+(50+v*22).toFixed(0)+'%)';
        ctx.strokeStyle=col; ctx.shadowColor=col;
        ctx.beginPath(); ctx.moveTo(cx+c*R0,cy+s*R0); ctx.lineTo(cx+c*(R0+len),cy+s*(R0+len)); ctx.stroke();}
      ctx.restore();}
    if(opts.audio&&opts.audio.duration&&isFinite(opts.audio.duration)){var p=opts.audio.currentTime/opts.audio.duration;
      ctx.save(); ctx.strokeStyle='rgba(201,162,39,.85)'; ctx.lineWidth=W*0.009; ctx.lineCap='round';
      ctx.beginPath(); ctx.arc(cx,cy,R0*0.9,-1.5708,-1.5708+p*6.2832); ctx.stroke(); ctx.restore();}
    for(var k=0;k<sparks.length;k++){var sp=sparks[k]; sp.x+=sp.vx; sp.y+=sp.vy; sp.vx*=0.96; sp.vy*=0.96; sp.life-=0.035;
      ctx.globalAlpha=Math.max(0,sp.life); ctx.fillStyle='#ffe79a'; ctx.beginPath(); ctx.arc(sp.x,sp.y,W*0.006,0,6.2832); ctx.fill();}
    ctx.globalAlpha=1; sparks=sparks.filter(function(s){return s.life>0;});
  }
  function spawn(k){var W=opts.canvas.width,cx=W/2,cy=W/2,R0=W*0.36,n=Math.min(10,Math.floor(k*40));
    for(var j=0;j<n;j++){var a=Math.random()*6.2832, sp=W*0.012*(1+Math.random()*2.5);
      sparks.push({x:cx+Math.cos(a)*R0,y:cy+Math.sin(a)*R0,vx:Math.cos(a)*sp,vy:Math.sin(a)*sp,life:1});}}
  frame();
}

// Scrub the track by dragging the record (DJ-style). A tap (no real drag) calls
// onTap instead, so the disk still works as a play/pause button.
function ttScrub(opts){
  var v=opts.vinyl, a=opts.audio, secPerRev=opts.secPerRev||4, drag=false, started=false, last=0, moved=0, rot=0;
  function pt(e){return (e.touches&&e.touches[0])?e.touches[0]:e;}
  function ang(e){var p=pt(e), r=v.getBoundingClientRect();
    return Math.atan2(p.clientY-(r.top+r.height/2), p.clientX-(r.left+r.width/2));}
  function curRot(){var m=getComputedStyle(v).transform;
    if(m&&m.indexOf('matrix')===0){var n=m.slice(7,-1).split(','); return Math.atan2(parseFloat(n[1]),parseFloat(n[0]))*180/Math.PI;} return 0;}
  function down(e){drag=true; started=false; moved=0; last=ang(e); if(e.cancelable)e.preventDefault();}
  function move(e){if(!drag)return; var na=ang(e), d=na-last;
    if(d>Math.PI)d-=6.2832; else if(d<-Math.PI)d+=6.2832; last=na; moved+=Math.abs(d);
    if(!started){ if(moved<0.04)return; started=true; rot=curRot(); v.style.animation='none'; }  // only grab the disk on a real drag
    rot+=d*57.2958; v.style.transform='rotate('+rot+'deg)';
    if(a.duration&&isFinite(a.duration)){a.currentTime=Math.max(0,Math.min(a.duration-0.05, a.currentTime+d/6.2832*secPerRev));}
    if(e.cancelable)e.preventDefault();}
  function up(){if(!drag)return; drag=false; if(started){v.style.animation=''; v.style.transform='';}
    else if(opts.onTap)opts.onTap();}
  v.addEventListener('mousedown',down); window.addEventListener('mousemove',move); window.addEventListener('mouseup',up);
  v.addEventListener('touchstart',down,{passive:false}); window.addEventListener('touchmove',move,{passive:false}); window.addEventListener('touchend',up);
}
"""


_PLAYER_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>__PACK_NAME__</title>
<style>
  :root{--bg:#0d0d10;--panel:#15151b;--line:#26262f;--txt:#e7e7ea;--dim:#8a8a96;--accent:#c9a227;}
  *{box-sizing:border-box;}
  body{margin:0;background:radial-gradient(1100px 560px at 50% -8%,#1c1c24,var(--bg));color:var(--txt);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
  .wrap{max-width:540px;margin:0 auto;padding:32px 20px 80px;text-align:center;}
  h1{font-size:24px;letter-spacing:.02em;margin:0 0 4px;}
  .by{color:var(--dim);margin:0 0 18px;letter-spacing:.03em;}
  .by b{color:var(--accent);font-weight:600;}
  .reelbtn{position:fixed;top:12px;right:12px;z-index:9;background:#20202a;color:var(--txt);
    border:1px solid var(--line);border-radius:20px;padding:7px 14px;font-size:13px;cursor:pointer;}
  .reelbtn:hover{border-color:var(--accent);}
  /* deck + record are sized in % of .deck, so reel mode just scales .deck */
  .deck{position:relative;width:330px;height:330px;margin:8px auto 14px;}
  #viz{position:absolute;inset:0;width:100%;height:100%;}
  .vinyl{position:absolute;left:14%;top:14%;width:72%;height:72%;border-radius:50%;
    background:repeating-radial-gradient(circle at 50% 50%,#0c0c0e 0 0.85%,#191920 0.85% 1.7%),
      radial-gradient(circle at 38% 32%,#2a2a31,#000 72%);
    box-shadow:0 16px 46px rgba(0,0,0,.6),inset 0 0 0 2px #000;
    animation:spin 3.4s linear infinite;animation-play-state:paused;cursor:pointer;}
  .vinyl.spin{animation-play-state:running;}
  @keyframes spin{to{transform:rotate(360deg);}}
  .vinyl .label{position:absolute;left:30%;top:30%;width:40%;height:40%;border-radius:50%;
    background:radial-gradient(circle at 50% 34%,var(--accent),#8a6f17);color:#1a1405;display:flex;
    align-items:center;justify-content:center;font-weight:800;letter-spacing:.05em;text-transform:uppercase;
    font-size:clamp(11px,3.5vmin,18px);padding:6%;overflow:hidden;text-align:center;
    box-shadow:inset 0 0 0 2px rgba(0,0,0,.25);background-size:cover;background-position:center;}
  .vinyl .label.cover{background-color:#000;}
  .vinyl .hole{position:absolute;left:47.5%;top:47.5%;width:5%;height:5%;border-radius:50%;
    background:#000;z-index:2;box-shadow:0 0 0 0.6vmin #b6911f;}
  .arm{position:absolute;right:2%;top:0;width:47%;height:6%;border-radius:3px;
    background:linear-gradient(#43434f,#23232b);transform-origin:100% 50%;
    transform:rotate(8deg);transition:transform .6s cubic-bezier(.4,1.3,.5,1);z-index:3;}
  .arm.on{transform:rotate(-32deg);}  /* playing: needle swings DOWN onto the grooves */
  .arm:before{content:"";position:absolute;right:-10%;top:-110%;width:26%;aspect-ratio:1;border-radius:50%;
    background:radial-gradient(circle at 40% 35%,#3a3a44,#1f1f26);border:1px solid var(--line);}
  .arm:after{content:"";position:absolute;left:-4%;top:-40%;width:14%;aspect-ratio:1;border-radius:2px;
    background:#2a2a32;border:1px solid var(--line);}
  .gloss{position:absolute;left:14%;top:14%;width:72%;height:72%;border-radius:50%;pointer-events:none;z-index:2;
    background:linear-gradient(115deg,transparent 42%,rgba(255,255,255,.08) 50%,transparent 58%);
    animation:sheen 3.6s ease-in-out infinite;}
  @keyframes sheen{0%,100%{opacity:.35;}50%{opacity:.85;}}
  .fx{position:fixed;inset:0;pointer-events:none;z-index:8;
    background:radial-gradient(125% 85% at 50% 42%,transparent 52%,rgba(0,0,0,.5) 100%);}
  .fx::after{content:"";position:absolute;inset:0;opacity:.045;mix-blend-mode:overlay;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");}
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
  ol.list li{display:flex;gap:11px;align-items:baseline;padding:11px 13px;border:1px solid var(--line);
    border-radius:9px;margin-bottom:9px;cursor:pointer;background:var(--panel);}
  ol.list li:hover{border-color:#4a4a55;}
  ol.list li.active{border-color:var(--accent);background:#1d1d12;}
  ol.list .num{color:var(--accent);font-weight:600;font-variant-numeric:tabular-nums;}
  ol.list .ti{font-weight:500;}
  ol.list .me{color:var(--dim);font-size:13px;margin-left:auto;white-space:nowrap;}
  .contact{color:var(--dim);margin-top:24px;}
  footer{color:var(--dim);font-size:12px;margin-top:30px;}
  /* 9:16 reel mode — fill the frame for a vertical screen-record */
  body.reel .wrap{max-width:none;min-height:100vh;display:flex;flex-direction:column;
    align-items:center;justify-content:center;padding:16px;}
  body.reel h1,body.reel .by,body.reel ol.list,body.reel footer,body.reel .contact{display:none;}
  body.reel .deck{width:min(88vw,64vh);height:min(88vw,64vh);margin:0 auto 26px;}
  body.reel .now{transform:scale(1.15);margin-bottom:0;}
</style></head>
<body>
<button class="reelbtn" id="reelbtn">⤢ Reel</button>
<div class="wrap">
  <h1>__PACK_NAME__</h1>
  <p class="by">Produced by <b>__PRODUCER__</b> · __MADE__ · __NBEATS__ beats</p>
  <div class="deck" id="deck">
    <canvas id="viz"></canvas>
    <div class="vinyl" id="vinyl">__LABEL_HTML__<div class="hole"></div></div>
    <div class="gloss"></div>
    <div class="arm" id="arm"></div>
  </div>
  <div class="now">
    <button class="play" id="play">&#9654;</button>
    <span class="bpmdot" id="bpmdot"></span>
    <div class="nowinfo"><div class="nt" id="nt">Tap a beat ↓</div><div class="nm" id="nm"></div></div>
  </div>
  <ol class="list" id="list"></ol>
  __CONTACT__
  <footer>Made with releases · serve this folder or drop it on a static host to share</footer>
</div>
<div class="fx"></div>
<audio id="audio"></audio>
<script>
__VIZ_JS__
const TRACKS = __TRACKS__;
const audio=document.getElementById('audio'),vinyl=document.getElementById('vinyl'),arm=document.getElementById('arm');
const playBtn=document.getElementById('play'),nt=document.getElementById('nt'),nm=document.getElementById('nm'),list=document.getElementById('list');
let cur=-1,actx,analyser,data;
TRACKS.forEach((t,i)=>{const li=document.createElement('li');
  li.innerHTML='<span class="num"></span><span class="ti"></span><span class="me"></span>';
  li.querySelector('.num').textContent=String(i+1).padStart(2,'0');
  li.querySelector('.ti').textContent=t.t; li.querySelector('.me').textContent=t.m;
  li.onclick=()=>select(i); list.appendChild(li);});
function select(i){cur=i;const t=TRACKS[i];audio.src=encodeURI(t.f);
  nt.textContent=t.t;nm.textContent=t.m;
  const nfo=document.querySelector('.nowinfo');nfo.classList.remove('nfin');void nfo.offsetWidth;nfo.classList.add('nfin');
  const dot=document.getElementById('bpmdot');if(t.b){dot.style.display='inline-block';dot.style.animationDuration=(60/t.b).toFixed(3)+'s';}else{dot.style.display='none';}
  [...list.children].forEach((li,j)=>li.classList.toggle('active',j===i));
  audio.play().catch(()=>{});}
function setPlaying(p){vinyl.classList.toggle('spin',p);arm.classList.toggle('on',p);playBtn.innerHTML=p?'&#10074;&#10074;':'&#9654;';}
audio.addEventListener('play',()=>{initViz();setPlaying(true);});
audio.addEventListener('pause',()=>setPlaying(false));
audio.addEventListener('ended',()=>{cur<TRACKS.length-1?select(cur+1):setPlaying(false);});
function togglePlay(){if(cur<0){select(0);return;}audio.paused?audio.play():audio.pause();}
playBtn.onclick=togglePlay;
ttScrub({vinyl:vinyl,audio:audio,secPerRev:4,onTap:togglePlay});
const reelbtn=document.getElementById('reelbtn');
reelbtn.onclick=()=>{const on=document.body.classList.toggle('reel');
  reelbtn.textContent=on?'✕ Exit':'⤢ Reel';size();};
const canvas=document.getElementById('viz');
function size(){const s=document.getElementById('deck').clientWidth;canvas.width=s*2;canvas.height=s*2;}
size();addEventListener('resize',size);
function initViz(){
  if(actx){if(actx.state==='suspended')actx.resume();return;}
  try{actx=new (window.AudioContext||window.webkitAudioContext)();
    const src=actx.createMediaElementSource(audio);
    analyser=actx.createAnalyser();analyser.fftSize=256;analyser.smoothingTimeConstant=.8;
    src.connect(analyser);analyser.connect(actx.destination);
  }catch(e){/* file:// or unsupported — vinyl still spins, audio still plays */}
}
ttRun({canvas:canvas,audio:audio,getAnalyser:function(){return analyser;},
  scene:document.querySelector('.wrap'),label:document.querySelector('#vinyl .label'),
  reelGet:function(){return document.body.classList.contains('reel');},sparksOn:true});
</script>
</body></html>
"""


def render_index_html(
    tracks: list[PackTrack], meta: PackMeta, filenames: list[str], cover: str | None = None
) -> str:
    items = [{"t": t.title, "m": _meta_str(t), "f": fn, "b": t.bpm or 0}
             for t, fn in zip(tracks, filenames)]
    # JSON for the <script> context: escape <, >, & so a title can't break out of it.
    tracks_json = (
        json.dumps(items, ensure_ascii=False)
        .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )
    if cover:
        # cover is a pack-relative filename we control (e.g. "cover.jpg"); show it
        # as the vinyl label (picture-disc style) instead of the producer text.
        label_html = f'<div class="label cover" style="background-image:url(\'{html.escape(cover, quote=True)}\')"></div>'
    else:
        text = (meta.producer or "").strip()[:16] or "Beats"
        label_html = f'<div class="label">{html.escape(text)}</div>'
    contact = f'<p class="contact">{html.escape(meta.contact)}</p>' if meta.contact else ""
    subs = {
        "__PACK_NAME__": html.escape(meta.name),
        "__PRODUCER__": html.escape(meta.producer),
        "__MADE__": html.escape(meta.made_on),
        "__NBEATS__": str(len(tracks)),
        "__LABEL_HTML__": label_html,
        "__CONTACT__": contact,
        "__VIZ_JS__": TURNTABLE_JS,
        "__TRACKS__": tracks_json,
    }
    out = _PLAYER_TEMPLATE
    for token, value in subs.items():
        out = out.replace(token, value)
    return out


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def build_pack(
    tracks: list[PackTrack], out_dir: Path, meta: PackMeta, cover_src: Path | None = None
) -> list[str]:
    """Write the pack into ``out_dir`` (created): clean-named audio copies +
    index.html + tracklist.txt. ``cover_src``, if an image, is copied in as the
    vinyl-label cover art. Returns the list of audio filenames written. Read-only
    on the sources (copy only)."""
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
    cover_name = None
    if cover_src is not None and cover_src.is_file() and is_image(cover_src):
        cover_name = "cover" + cover_src.suffix.lower()
        shutil.copy2(cover_src, out_dir / cover_name)
    (out_dir / "index.html").write_text(
        render_index_html(tracks, meta, filenames, cover=cover_name), encoding="utf-8"
    )
    (out_dir / "tracklist.txt").write_text(render_tracklist_txt(tracks, meta), encoding="utf-8")
    return filenames
