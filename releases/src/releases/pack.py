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


# Shared turntable visualizer + beat-reactive effects. Lives here as the single
# source of truth: the exported pack inlines it; the web app serves it at
# /api/turntable.js. ``ttRun(opts)`` runs one rAF loop that draws a glowing,
# frequency-coloured mirrored spectrum + a progress ring + a bass-driven halo and
# sparks, and (in reel mode) pulses the label, breathes, and shakes the scene to
# the kick. opts = {canvas, audio, getAnalyser, scene, label, reelGet, sparksOn}.
TURNTABLE_JS = r"""
function ttRun(opts){
  var ctx=opts.canvas.getContext('2d'), fxctx=null;  // fxctx = full-screen particle canvas, if provided
  var bassAvg=0, eLong=0, eMid=0, shake=0, punch=0, flash=0, hue=42, lastDrop=0, seeded=false, dataArr=null, curEnergy=0;
  var sparks=[], embers=[], shocks=[], smoke=[], smoothV=null, idleT=0;  // idleT drives the always-on motion
  var fxw=0, fxh=0, rcx=0, rcy=0, ref=0, fxLeft=0, fxTop=0, sclx=1, scly=1, curHeat=0, stylusAng=-0.7, stylusX=0, stylusY=0, hasStylus=false;  // particle field + record centre/scale + scene scale + heat + live stylus contact point (fx px)
  var fxSmoke=true, fxParticles=true, fxShake=true, fxHeat=true;  // per-effect on/off (set each frame from the page's toggles)
  function on(n){return !opts.fxOn||opts.fxOn(n);}  // an effect is ON unless the page switched it off
  function nowMs(){return (window.performance&&performance.now)?performance.now():Date.now();}
  function rgbHue(r,g,b){r/=255;g/=255;b/=255;var mx=Math.max(r,g,b),mn=Math.min(r,g,b),d=mx-mn,h=0;
    if(d){if(mx===r)h=((g-b)/d+6)%6;else if(mx===g)h=(b-r)/d+2;else h=(r-g)/d+4;h*=60;}return h;}
  function seed(url){var img=new Image();img.crossOrigin='anonymous';
    img.onload=function(){try{var cc=document.createElement('canvas');cc.width=cc.height=14;var c2=cc.getContext('2d');
      c2.drawImage(img,0,0,14,14);var px=c2.getImageData(0,0,14,14).data,r=0,g=0,b=0,n=0;
      for(var i=0;i<px.length;i+=4){if(px[i+3]>10){r+=px[i];g+=px[i+1];b+=px[i+2];n++;}}
      if(n)hue=rgbHue(r/n,g/n,b/n);}catch(e){}};img.src=url;}
  // beat-drop payoff: a burst that flings outward across the WHOLE screen + two clean rings
  function onDrop(energy){flash=1;punch=1;shake=15;
    if(!fxParticles)return;  // keep the flash/shake payoff, drop the spark+ring particles
    for(var i=0;i<80;i++){var a=Math.random()*6.2832,sp=fxw*0.007*(1+Math.random()*4.5);
      sparks.push({x:rcx+Math.cos(a)*ref*0.18,y:rcy+Math.sin(a)*ref*0.18,vx:Math.cos(a)*sp,vy:Math.sin(a)*sp-fxh*0.002,life:1,h:(hue+Math.random()*80)%360,big:true});}
    shocks.push({x:rcx,y:rcy,r:ref*0.45,life:1}); shocks.push({x:rcx,y:rcy,r:ref*0.25,life:0.8});}
  function frame(){
    requestAnimationFrame(frame);
    idleT+=0.016;
    if(!seeded){var cu=opts.getCover&&opts.getCover();if(cu){seeded=true;seed(cu);}else if(!opts.getCover){seeded=true;}}
    var W=opts.canvas.width; if(!W){return;}
    if(opts.pauseDraw&&opts.pauseDraw())return;  // an export is using the main thread — skip the live draw
    // size + locate the full-screen particle field (record centre in the field's own px)
    var fx=opts.fxCanvas;
    if(fx && !fx.clientWidth)return;  // overlay hidden (display:none) — skip; avoids particles spawned at the corner
    if(fx){if(!fxctx)fxctx=fx.getContext('2d');
      if(fx.clientWidth&&fx.width!==fx.clientWidth)fx.width=fx.clientWidth;
      if(fx.clientHeight&&fx.height!==fx.clientHeight)fx.height=fx.clientHeight;
      var fr=fx.getBoundingClientRect(), dr=opts.canvas.getBoundingClientRect();
      sclx=fr.width?fx.width/fr.width:1; scly=fr.height?fx.height/fr.height:1;  // backing px per on-screen px (handles a scaled scene, e.g. reel mode / kick punch)
      fxw=fx.width; fxh=fx.height; ref=(dr.width||W)*sclx; fxLeft=fr.left; fxTop=fr.top;
      rcx=(dr.left+dr.width/2-fr.left)*sclx; rcy=(dr.top+dr.height/2-fr.top)*scly;
    }else{fxw=W;fxh=W;ref=W;rcx=W/2;rcy=W/2;fxLeft=0;fxTop=0;sclx=1;scly=1;}
    var playing=!!(opts.audio&&!opts.audio.paused);
    fxSmoke=on('smoke');fxParticles=on('particles');fxShake=on('shake');fxHeat=on('heat');
    var an=opts.getAnalyser(), bass=0, energy=0;
    if(an){if(!dataArr||dataArr.length!==an.frequencyBinCount){dataArr=new Uint8Array(an.frequencyBinCount);}
      an.getByteFrequencyData(dataArr);
      for(var i=0;i<5;i++){bass+=dataArr[i];}bass/=1275;
      for(var j=0;j<dataArr.length;j++){energy+=dataArr[j];}energy/=dataArr.length*255;}
    curEnergy=energy;
    if(eLong===0&&energy>0)eLong=energy;  // seed the running average so a track's intro isn't read as one long build-up
    bassAvg=bassAvg*0.9+bass*0.1; eLong=eLong*0.985+energy*0.015;
    var kick=Math.max(0,bass-bassAvg-0.05);
    if(energy>eLong*1.45+0.12 && energy>0.30 && nowMs()-lastDrop>1400){lastDrop=nowMs();onDrop(energy);}
    hue=(hue+0.04+energy*0.45)%360;  // a touch slower so the colour drift reads as calm, not strobing
    flash*=0.86; punch*=0.9; shake*=0.82;
    if(kick>0.05){shake=Math.max(shake,Math.min(11,kick*34)); if(opts.sparksOn&&fxParticles)spawn(kick);}  // bass/kick → screen shake
    var breath=0.5+0.5*Math.sin(idleT*0.9);  // gentle idle pulse, ~7s cycle
    var lbl=opts.getLabel?opts.getLabel():opts.label;
    if(lbl){var ls=1+Math.min(0.2,kick*1.4)+punch*0.06+(playing?0:breath*0.02);
      lbl.style.transform='scale('+ls.toFixed(3)+')';}
    if(opts.scene){var sx=fxShake?(Math.random()*2-1)*shake:0,sy=fxShake?(Math.random()*2-1)*shake:0;
      // ALWAYS set a transform (identity when idle) — never clear it to ''. Toggling
      // the overlay's transform on/off churns its GPU layer, which momentarily flashes
      // the page behind it. A constant transform keeps the layer stable.
      if(opts.reelGet&&opts.reelGet())
        opts.scene.style.transform='translate('+sx.toFixed(1)+'px,'+sy.toFixed(1)+'px) scale('+(1.0+Math.min(0.03,energy*0.04)+punch*0.06).toFixed(3)+')';  // gentle — keep the whole deck in frame
      else
        opts.scene.style.transform='translate('+sx.toFixed(1)+'px,'+sy.toFixed(1)+'px) scale('+(1+punch*0.04).toFixed(3)+')';}
    if(opts.flash){opts.flash.style.opacity=Math.min(0.7,flash).toFixed(3);
      if(flash>0.02)opts.flash.style.background='radial-gradient(circle at 50% 45%,hsla('+hue.toFixed(0)+',90%,75%,.9),transparent 70%)';}
    if(playing&&fxParticles)ambient(energy);  // embers only while music plays — idle stays clean
    // brake-disc heat: builds toward the end of the track; drives the cassette
    // reels' red glow (via the --heat CSS var) and the smoke colour.
    var dur=(opts.audio&&opts.audio.duration&&isFinite(opts.audio.duration))?opts.audio.duration:0;
    var ct=opts.audio?(opts.audio.currentTime||0):0;
    var pr=dur?ct/dur:0, heat=Math.min(1,pr*3);  // cassette reels reach full red by ~a third in
    curHeat=dur?Math.min(1,Math.max(0,((ct-20)/dur)*3)):0;  // vinyl groove stays cold for the first ~20s, then ramps
    var build=Math.max(0,energy-eLong*1.05);  // energy rising above its running average = a build-up
    if(opts.scene)opts.scene.style.setProperty('--heat',(fxHeat?heat:0).toFixed(3));  // heat toggle → reels' red glow
    if(opts.scene)opts.scene.style.setProperty('--prog',pr.toFixed(4));  // tonearm tracks inward with track progress
    var cassette=opts.getSkin&&opts.getSkin()==='cassette';
    var pts=opts.smokeAt?opts.smokeAt():[];  // reels (cassette) or the stylus (vinyl)
    if(!cassette && playing && pts.length){  // the red contact circle sits exactly where the stylus tip meets the vinyl
      stylusX=(pts[0].x-fxLeft)*sclx; stylusY=(pts[0].y-fxTop)*scly; hasStylus=true;}
    if(playing && fxSmoke && opts.fxCanvas && pts.length){var prob=Math.min(0.7,build*6+heat*0.14)*(cassette?1.35:1);  // build-up driven; the cassette smokes a little more
      for(var s=0;s<pts.length;s++){if(Math.random()<prob)puff((pts[s].x-fxLeft)*sclx,(pts[s].y-fxTop)*scly,heat);}}
    // a sustained loud/full section = best guess at the chorus/hook -> an EXCESS of
    // particles streaming up from the bottom of the screen (not bursting off the deck)
    eMid+=(energy-eMid)*0.06;  // ~0.7s smoothing so transient kicks don't count, only sustained sections
    var chorus=Math.min(1,Math.max(0,(eMid-0.25)/0.30));
    if(playing && opts.sparksOn && fxParticles && chorus>0.05){
      var rise=Math.floor(chorus*chorus*9);  // up to ~9 per frame, rising from the bottom edge
      for(var ci=0;ci<rise && embers.length<300;ci++)
        embers.push({x:Math.random()*fxw,y:fxh+8,vx:(Math.random()*2-1)*fxw*0.0006,vy:-(fxh*0.0017)*(0.7+Math.random()*1.5)*(0.7+chorus*0.8),life:1,sz:ref*(0.004+Math.random()*0.013)});}
    draw(energy,kick,playing,breath);
    drawFX();  // particles, on the full-screen field
  }
  function ambient(energy){var cap=10+Math.floor(energy*60), n=1+Math.floor(energy*4);
    for(var q=0;q<n;q++){ if(embers.length<cap && Math.random()<0.5){
      embers.push({x:Math.random()*fxw,y:fxh+8,vx:(Math.random()*2-1)*fxw*0.0004,vy:-(fxh*0.0011)*(0.5+Math.random()*1.4)*(0.6+energy*1.3),life:1,sz:ref*(0.004+Math.random()*0.01)});}}}
  // a thin thread of smoke like the wisp off a match: rises in a wavering line and
  // barely widens; hotter = redder
  function puff(x,y,heat){if(smoke.length>120)return;
    smoke.push({bx:x,x:x,y:y,vy:-(fxh*0.0012)*(0.8+Math.random()*0.5),r:ref*0.003,life:1,heat:heat,ph:Math.random()*6.2832,amp:ref*(0.01+Math.random()*0.016)});}
  function draw(energy,kick,playing,breath){
    var W=opts.canvas.width,cx=W/2,cy=W/2,R0=W*0.36,maxOuter=W*0.475;
    ctx.clearRect(0,0,W,W);
    // halo — always present and breathing, so the deck never goes fully dark
    var halo=0.05+energy*0.25+breath*0.03;
    var g=ctx.createRadialGradient(cx,cy,R0*0.55,cx,cy,R0*(1.1+energy*0.45+breath*0.05));
    g.addColorStop(0,'hsla('+hue.toFixed(0)+',90%,55%,'+halo.toFixed(3)+')');g.addColorStop(1,'hsla('+hue.toFixed(0)+',90%,55%,0)');
    ctx.fillStyle=g;ctx.beginPath();ctx.arc(cx,cy,R0*1.35,0,6.2832);ctx.fill();
    var cassette=opts.getSkin&&opts.getSkin()==='cassette';
    if(!cassette && kick>0.02){ctx.save();ctx.globalAlpha=Math.min(0.9,kick*3.5);ctx.strokeStyle='hsl('+hue.toFixed(0)+',95%,72%)';
      ctx.lineWidth=W*0.006;ctx.beginPath();ctx.arc(cx,cy,R0*1.03,0,6.2832);ctx.stroke();ctx.restore();}
    // spectrum — eased frame-to-frame (flowing, not jittery), with an always-on
    // idle shimmer and, on the disk, a slow continuous revolve.
    var bars=cassette?80:96, half=bars/2, envAmp=playing?0.05:0.14, ringRot=idleT*0.08;
    if(!smoothV||smoothV.length!==bars){smoothV=new Float32Array(bars);}
    ctx.save();ctx.shadowBlur=W*0.006;ctx.lineCap='round';  // smaller blur = much cheaper per frame (96 bars)
    if(cassette){var baseY=W*0.85,ctw=W*0.82,clx=W*0.09;ctx.lineWidth=W*0.012;
      var prc=(opts.audio&&opts.audio.duration&&isFinite(opts.audio.duration))?opts.audio.currentTime/opts.audio.duration:0;
      for(var i=0;i<bars;i++){var idx=i<half?i:bars-1-i;
        var raw=dataArr?dataArr[Math.floor(idx/half*dataArr.length*0.7)]/255:0;
        var env=envAmp*(0.5+0.5*Math.sin(idleT*1.7+idx*0.5));
        smoothV[i]+=(Math.max(raw,env)-smoothV[i])*0.35;var v=smoothV[i];
        var fxp=i/(bars-1),x=clx+fxp*ctw,len=W*0.012+v*v*W*0.16,played=fxp<=prc;  // mirrored seek-style waveform: played part lit, rest dimmed
        var col=played?'hsl('+((hue+idx/half*46)%360).toFixed(0)+','+(80+v*20).toFixed(0)+'%,'+(56+v*20).toFixed(0)+'%)':'hsla(40,12%,'+(42+v*16).toFixed(0)+'%,.55)';
        ctx.strokeStyle=col;ctx.shadowColor=played?col:'transparent';ctx.beginPath();ctx.moveTo(x,baseY-len/2);ctx.lineTo(x,baseY+len/2);ctx.stroke();}}
    else{ctx.lineWidth=W*0.013;
      for(var b=0;b<bars;b++){var j2=b<half?b:bars-1-b;
        var raw2=dataArr?dataArr[Math.floor(j2/half*dataArr.length*0.7)]/255:0;
        var env2=envAmp*(0.5+0.5*Math.sin(idleT*1.7+j2*0.5));
        smoothV[b]+=(Math.max(raw2,env2)-smoothV[b])*0.35;var w2=smoothV[b];
        var l2=Math.min(W*0.012+w2*w2*W*0.11,maxOuter-R0),a2=b/bars*6.2832-1.5708+ringRot,c2=Math.cos(a2),s2=Math.sin(a2);
        var k2='hsl('+((hue+j2/half*40)%360).toFixed(0)+','+(72+w2*25).toFixed(0)+'%,'+(52+w2*20).toFixed(0)+'%)';
        ctx.strokeStyle=k2;ctx.shadowColor=k2;ctx.beginPath();ctx.moveTo(cx+c2*R0,cy+s2*R0);ctx.lineTo(cx+c2*(R0+l2),cy+s2*(R0+l2));ctx.stroke();}}
    ctx.restore();
    if(!cassette && opts.audio&&opts.audio.duration&&isFinite(opts.audio.duration)){var pr=opts.audio.currentTime/opts.audio.duration;
      ctx.save();ctx.strokeStyle='hsla('+hue.toFixed(0)+',90%,66%,.9)';ctx.lineCap='round';
      ctx.lineWidth=W*0.009;ctx.beginPath();ctx.arc(cx,cy,R0*0.9,-1.5708,-1.5708+pr*6.2832);ctx.stroke();
      ctx.restore();}  // cassette progress is shown by the lit portion of its waveform
  }
  // the stylus scorches a red groove into the vinyl as the record spins under it —
  // a charred ring + red-hot line + a bright contact ember, all building with heat.
  // Drawn on the full-screen field (above the opaque record) so it's actually visible.
  function drawTrail(c){var cassette=opts.getSkin&&opts.getSkin()==='cassette';
    if(cassette||!fxHeat||curHeat<=0.001)return;
    var h=curHeat;
    // the stylus tracks inward as the track plays (the arm sweeps via --prog), so the
    // contact radius shrinks from the outer edge toward the label — a real record groove.
    var contactR=hasStylus?Math.sqrt((stylusX-rcx)*(stylusX-rcx)+(stylusY-rcy)*(stylusY-rcy)):ref*0.34;
    contactR=Math.max(ref*0.2,Math.min(contactR,ref*0.36));
    var outerR=ref*0.36;  // the groove the needle started on (outer edge)
    // scorched band the needle has already crossed (outer → current), widening as it tracks in
    if(outerR-contactR>1){c.save();c.globalAlpha=Math.min(0.45,h*0.45);c.strokeStyle='rgba(42,10,4,1)';
      c.lineWidth=outerR-contactR;c.beginPath();c.arc(rcx,rcy,(outerR+contactR)/2,0,6.2832);c.stroke();c.restore();}
    // red-hot current groove ring at the contact radius
    c.save();c.globalAlpha=Math.min(0.85,h*0.85);c.shadowBlur=ref*0.05*h;c.shadowColor='rgba(255,60,0,1)';
    c.strokeStyle='hsl('+(16+h*8).toFixed(0)+',100%,'+(46+h*16).toFixed(0)+'%)';c.lineWidth=ref*0.012*(0.6+h);
    c.beginPath();c.arc(rcx,rcy,contactR,0,6.2832);c.stroke();c.restore();
    // the red contact circle sits at the end of the stylus, on the vinyl
    var sxp=hasStylus?stylusX:rcx+Math.cos(stylusAng)*contactR, syp=hasStylus?stylusY:rcy+Math.sin(stylusAng)*contactR, hr=ref*0.12*(0.45+h);
    var hg=c.createRadialGradient(sxp,syp,0,sxp,syp,hr);
    hg.addColorStop(0,'rgba(255,232,190,'+Math.min(0.95,0.35+h*0.6).toFixed(3)+')');
    hg.addColorStop(0.4,'rgba(255,80,0,'+Math.min(0.85,h*0.85).toFixed(3)+')');
    hg.addColorStop(1,'rgba(255,40,0,0)');
    c.fillStyle=hg;c.beginPath();c.arc(sxp,syp,hr,0,6.2832);c.fill();c.globalAlpha=1;}
  // particles live on a full-screen canvas (opts.fxCanvas) so the burst, embers and
  // shockwaves fill the whole viewport — not just the record box. Falls back to the
  // deck canvas when no field is supplied (drawn after draw(), which already cleared it).
  function drawFX(){
    var c=fxctx||ctx, e;
    if(fxctx)c.clearRect(0,0,fxw,fxh);
    if(fxctx)drawTrail(c);  // burnt groove + stylus ember (full-screen field only), under the smoke/particles
    for(var z=smoke.length-1;z>=0;z--){var pf=smoke[z];pf.y+=pf.vy;pf.vy*=0.997;
      var age=1-pf.life;pf.x=pf.bx+Math.sin(age*9+pf.ph)*pf.amp*age;pf.r=ref*0.003+age*ref*0.013;pf.life-=0.006;
      if(pf.life<=0){smoke.splice(z,1);continue;}
      var sr=Math.floor(120+pf.heat*135),sg=Math.floor(120-pf.heat*72),sb=Math.floor(120-pf.heat*96);
      var gg=c.createRadialGradient(pf.x,pf.y,0,pf.x,pf.y,pf.r);
      gg.addColorStop(0,'rgba('+sr+','+sg+','+sb+','+(pf.life*0.2).toFixed(3)+')');
      gg.addColorStop(1,'rgba('+sr+','+sg+','+sb+',0)');
      c.fillStyle=gg;c.beginPath();c.arc(pf.x,pf.y,pf.r,0,6.2832);c.fill();}
    c.globalAlpha=1;
    for(e=embers.length-1;e>=0;e--){var p=embers[e];p.x+=p.vx;p.y+=p.vy;p.life-=0.004;
      if(p.y<-14||p.life<=0){embers.splice(e,1);continue;}
      c.globalAlpha=p.life*0.4;c.fillStyle='hsl('+hue.toFixed(0)+',70%,62%)';
      c.beginPath();c.arc(p.x,p.y,p.sz,0,6.2832);c.fill();}
    c.globalAlpha=1;
    for(var k=sparks.length-1;k>=0;k--){var sp=sparks[k];sp.x+=sp.vx;sp.y+=sp.vy;if(sp.big)sp.vy+=fxh*0.0006;sp.vx*=0.975;sp.vy*=0.975;sp.life-=0.02;
      if(sp.life<=0){sparks.splice(k,1);continue;}
      c.globalAlpha=Math.max(0,sp.life);c.fillStyle=(sp.h!==undefined)?'hsl('+sp.h.toFixed(0)+',95%,66%)':'#ffe79a';
      c.beginPath();c.arc(sp.x,sp.y,ref*(sp.big?0.012:0.008),0,6.2832);c.fill();}
    for(var m=shocks.length-1;m>=0;m--){var sh=shocks[m];sh.r+=fxw*0.012;sh.life-=0.018;
      if(sh.life<=0){shocks.splice(m,1);continue;}
      c.save();c.globalAlpha=Math.max(0,sh.life*0.6);c.strokeStyle='hsl('+hue.toFixed(0)+',95%,72%)';
      c.lineWidth=fxw*0.004*sh.life;c.beginPath();c.arc(sh.x,sh.y,sh.r,0,6.2832);c.stroke();c.restore();}
    c.globalAlpha=1;
  }
  function spawn(k){var n=Math.min(14,Math.floor(k*26)+Math.floor(curEnergy*10));
    for(var j=0;j<n;j++){var a=Math.random()*6.2832,sp=fxw*0.004*(1+Math.random()*3);
      sparks.push({x:rcx+Math.cos(a)*ref*0.36,y:rcy+Math.sin(a)*ref*0.36,vx:Math.cos(a)*sp,vy:Math.sin(a)*sp,life:1,h:(hue+Math.random()*50)%360});}}
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

// ---- video export: draw the WHOLE turntable scene onto ONE canvas ----
// (disc/label/arm normally live as DOM; here they're drawn on the canvas so a
// captureStream() recording is clean — just the deck + effects, no UI chrome).
// st = {skin, hue, data, energy, pr, curHeat, armDeg, cover(Image), label, bottomFx(canvas)}.
function ttExportFrame(c,W,H,st){
  var bg=c.createLinearGradient(0,0,0,H);bg.addColorStop(0,'#181820');bg.addColorStop(1,'#0a0a0d');
  c.fillStyle=bg;c.fillRect(0,0,W,H);
  var S=Math.min(W*0.84,H*0.46),cx=W/2,cy=H*0.40;
  if(st.skin==='cassette')ttCassetteScene(c,cx,cy,S,st); else ttVinylScene(c,cx,cy,S,st);
  if(st.bottomFx)c.drawImage(st.bottomFx,0,0,st.bottomFx.width,st.bottomFx.height,0,0,W,H);
}
function ttVinylScene(c,cx,cy,S,st){
  var R=S*0.36,hue=st.hue||42,da=st.data,e=st.energy||0;
  var halo=0.06+e*0.25;
  var g=c.createRadialGradient(cx,cy,R*0.55,cx,cy,R*1.5);
  g.addColorStop(0,'hsla('+hue.toFixed(0)+',90%,55%,'+halo.toFixed(3)+')');g.addColorStop(1,'hsla('+hue.toFixed(0)+',90%,55%,0)');
  c.fillStyle=g;c.beginPath();c.arc(cx,cy,R*1.5,0,6.2832);c.fill();
  var disc=c.createRadialGradient(cx-R*0.28,cy-R*0.36,R*0.1,cx,cy,R);
  disc.addColorStop(0,'#2a2a31');disc.addColorStop(0.72,'#000');disc.addColorStop(1,'#050506');
  c.fillStyle=disc;c.beginPath();c.arc(cx,cy,R,0,6.2832);c.fill();
  c.strokeStyle='rgba(255,255,255,0.045)';c.lineWidth=Math.max(1,S*0.0035);
  for(var gr=R*0.42;gr<R*0.97;gr+=S*0.012){c.beginPath();c.arc(cx,cy,gr,0,6.2832);c.stroke();}
  var lr=R*0.4;
  if(st.cover&&st.cover.width){c.save();c.beginPath();c.arc(cx,cy,lr,0,6.2832);c.clip();
    c.drawImage(st.cover,cx-lr,cy-lr,lr*2,lr*2);c.restore();}
  else{var lg=c.createRadialGradient(cx,cy-lr*0.3,lr*0.1,cx,cy,lr);
    lg.addColorStop(0,'#e8c34a');lg.addColorStop(1,'#8a6f17');c.fillStyle=lg;c.beginPath();c.arc(cx,cy,lr,0,6.2832);c.fill();
    if(st.label){c.fillStyle='#1a1405';c.font='800 '+(lr*0.4).toFixed(0)+'px -apple-system,Helvetica,sans-serif';
      c.textAlign='center';c.textBaseline='middle';c.fillText(String(st.label).toUpperCase().slice(0,8),cx,cy);}}
  c.fillStyle='#000';c.beginPath();c.arc(cx,cy,R*0.05,0,6.2832);c.fill();
  if(da){var bars=96,half=48;c.save();c.lineCap='round';c.lineWidth=S*0.013;c.shadowBlur=S*0.011;
    for(var b=0;b<bars;b++){var j=b<half?b:bars-1-b;var v=da[Math.floor(j/half*da.length*0.7)]/255;
      var l=R*(0.03+v*v*0.3),a=b/bars*6.2832-1.5708,co=Math.cos(a),si=Math.sin(a);
      var col='hsl('+((hue+j/half*40)%360).toFixed(0)+','+(72+v*25).toFixed(0)+'%,'+(52+v*20).toFixed(0)+'%)';
      c.strokeStyle=col;c.shadowColor=col;c.beginPath();c.moveTo(cx+co*R,cy+si*R);c.lineTo(cx+co*(R+l),cy+si*(R+l));c.stroke();}
    c.restore();}
  // tonearm — pivot above-right, sweeping in with st.armDeg (-21 outer .. -35 inner)
  var pvx=cx+R*1.02,pvy=cy-R*1.04,len=R*1.34,ang=((st.armDeg||-24))*Math.PI/180;
  var tx=pvx-Math.cos(ang)*len,ty=pvy+Math.sin(ang)*len;
  c.save();c.lineCap='round';c.strokeStyle='#5a5a66';c.lineWidth=S*0.02;
  c.beginPath();c.moveTo(pvx,pvy);c.lineTo(tx,ty);c.stroke();
  c.fillStyle='#3a3a44';c.beginPath();c.arc(pvx,pvy,S*0.03,0,6.2832);c.fill();
  c.fillStyle='#52525e';c.beginPath();c.arc(tx,ty,S*0.024,0,6.2832);c.fill();c.restore();
}
function ttCassetteScene(c,cx,cy,S,st){
  var w=S*0.92,h=S*0.56,x=cx-w/2,y=cy-h/2,hue=st.hue||42,da=st.data;
  var bg=c.createLinearGradient(x,y,x,y+h);bg.addColorStop(0,'#34343f');bg.addColorStop(1,'#16161c');
  c.fillStyle=bg;ttRoundRect(c,x,y,w,h,S*0.04);c.fill();
  c.fillStyle='#c9a227';ttRoundRect(c,x+w*0.09,y+h*0.08,w*0.82,h*0.26,S*0.015);c.fill();
  if(st.label){c.fillStyle='#1a1405';c.font='800 '+(h*0.13).toFixed(0)+'px -apple-system,sans-serif';
    c.textAlign='center';c.textBaseline='middle';c.fillText(String(st.label).toUpperCase().slice(0,10),cx,y+h*0.21);}
  var wy=y+h*0.66,wr=h*0.22;c.fillStyle='#0b0b0f';ttRoundRect(c,x+w*0.11,wy-wr*1.1,w*0.78,wr*2.2,S*0.02);c.fill();
  [cx-w*0.22,cx+w*0.22].forEach(function(rx){c.save();c.translate(rx,wy);
    c.fillStyle='#2a2a34';c.beginPath();c.arc(0,0,wr,0,6.2832);c.fill();
    c.fillStyle='#14141a';for(var k=0;k<8;k++){c.save();c.rotate(k/8*6.2832);c.fillRect(-wr*0.08,-wr*0.9,wr*0.16,wr*0.5);c.restore();}
    c.fillStyle='#000';c.beginPath();c.arc(0,0,wr*0.16,0,6.2832);c.fill();c.restore();});
  if(da){var bars=80,half=40,baseY=cy+h*0.62+S*0.06;c.save();c.lineCap='round';c.lineWidth=S*0.012;c.shadowBlur=S*0.01;
    for(var i=0;i<bars;i++){var idx=i<half?i:bars-1-i;var v=da[Math.floor(idx/half*da.length*0.7)]/255;
      var px=cx-S*0.41+(i/(bars-1))*S*0.82,len=S*0.012+v*v*S*0.16;
      var col='hsl('+((hue+idx/half*46)%360).toFixed(0)+',80%,'+(56+v*20).toFixed(0)+'%)';
      c.strokeStyle=col;c.shadowColor=col;c.beginPath();c.moveTo(px,baseY-len/2);c.lineTo(px,baseY+len/2);c.stroke();}
    c.restore();}
}
function ttRoundRect(c,x,y,w,h,r){c.beginPath();c.moveTo(x+r,y);c.arcTo(x+w,y,x+w,y+h,r);
  c.arcTo(x+w,y+h,x,y+h,r);c.arcTo(x,y+h,x,y,r);c.arcTo(x,y,x+w,y,r);c.closePath();}
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
  /* deck + record are sized in % of .deck, so reel mode just scales .deck */
  .deck{position:relative;width:330px;height:330px;margin:8px auto 14px;animation:float 7s ease-in-out infinite;}
  @keyframes float{0%,100%{transform:translateY(-4px);}50%{transform:translateY(4px);}}
  #viz{position:absolute;inset:0;width:100%;height:100%;}
  .vinyl{position:absolute;left:14%;top:14%;width:72%;height:72%;border-radius:50%;
    background:
      conic-gradient(from 0deg,rgba(255,255,255,0) 0deg,rgba(255,255,255,.07) 30deg,rgba(255,255,255,0) 72deg,rgba(255,255,255,0) 205deg,rgba(255,255,255,.05) 235deg,rgba(255,255,255,0) 280deg),
      repeating-radial-gradient(circle at 50% 50%,#0c0c0e 0 0.85%,#191920 0.85% 1.7%),
      radial-gradient(circle at 38% 32%,#2a2a31,#000 72%);
    box-shadow:0 16px 46px rgba(0,0,0,.6),inset 0 0 0 2px #000,inset 0 0 16px 3px rgba(255,255,255,.05);
    animation:spin 3.4s linear infinite;animation-play-state:paused;cursor:pointer;}
  body.playing .vinyl{animation-play-state:running;}
  @keyframes spin{to{transform:rotate(360deg);}}
  .vinyl .label{position:absolute;left:30%;top:30%;width:40%;height:40%;border-radius:50%;
    background:radial-gradient(circle at 50% 34%,var(--accent),#8a6f17);color:#1a1405;display:flex;
    align-items:center;justify-content:center;font-weight:800;letter-spacing:.05em;text-transform:uppercase;
    font-size:clamp(11px,3.5vmin,18px);padding:6%;overflow:hidden;text-align:center;
    box-shadow:inset 0 0 0 2px rgba(0,0,0,.25),0 0 0 3px rgba(255,255,255,.1);background-size:cover;background-position:center;}
  .vinyl .label.cover{background-color:#000;}
  .vinyl .hole{position:absolute;left:47.5%;top:47.5%;width:5%;height:5%;border-radius:50%;
    background:#000;z-index:2;box-shadow:0 0 0 0.6vmin #b6911f;}
  .arm{position:absolute;right:4%;top:2%;width:46%;height:3.2%;border-radius:4px;z-index:3;
    background:linear-gradient(180deg,#60606c,#2a2a32);transform-origin:100% 50%;
    transform:rotate(8deg);transition:transform .6s cubic-bezier(.4,1.3,.5,1);box-shadow:0 2px 7px rgba(0,0,0,.5);}
  .arm.on{transform:rotate(calc(-21deg - var(--prog,0)*14deg));}  /* playing: needle on the grooves, sweeping inward with progress */
  .arm:before{content:"";position:absolute;right:-12%;top:50%;width:26%;aspect-ratio:1;border-radius:50%;transform:translateY(-50%);
    background:radial-gradient(circle at 38% 32%,#52525e,#1c1c22);border:1px solid #000;box-shadow:0 2px 6px rgba(0,0,0,.5);}
  .arm:after{content:"";position:absolute;left:-2%;top:30%;width:12%;height:240%;border-radius:2px;transform:rotate(24deg);
    background:linear-gradient(#3a3a44,#191920);border:1px solid #000;}
  .arm .tip{position:absolute;left:0;top:50%;width:1px;height:1px;}  /* stylus anchor for the smoke origin */
  /* cassette skin (toggled with .cassette-mode) */
  .cassette{position:absolute;left:6%;top:24%;width:88%;height:52%;border-radius:14px;display:none;z-index:1;
    background:linear-gradient(165deg,#34343f,#16161c);border:1px solid #000;
    box-shadow:0 16px 46px rgba(0,0,0,.6),inset 0 0 0 2px rgba(255,255,255,.05);}
  body.cassette-mode .cassette{display:block;}
  body.cassette-mode .vinyl,body.cassette-mode .arm,body.cassette-mode .gloss{display:none;}
  .cassette .clabel{position:absolute;left:9%;right:9%;top:8%;height:26%;border-radius:6px;overflow:hidden;
    background:radial-gradient(circle at 50% 30%,var(--accent),#8a6f17);background-size:cover;background-position:center;
    display:flex;align-items:center;justify-content:center;color:#1a1405;font-weight:800;text-transform:uppercase;
    letter-spacing:.05em;font-size:clamp(10px,3vmin,15px);box-shadow:inset 0 0 0 2px rgba(0,0,0,.2);}
  .cassette .clabel.cover{color:transparent;}
  .cassette .win{position:absolute;left:11%;right:11%;bottom:15%;height:48%;border-radius:10px;background:#0b0b0f;
    box-shadow:inset 0 0 0 2px #000,inset 0 5px 14px rgba(0,0,0,.7);display:flex;align-items:center;justify-content:space-between;padding:0 9%;}
  .cassette .reel{width:31%;aspect-ratio:1;border-radius:50%;position:relative;
    background:repeating-conic-gradient(#34343e 0 18deg,#14141a 18deg 36deg);
    box-shadow:inset 0 0 0 3px #000;animation:spin 1.7s linear infinite;animation-play-state:paused;}
  /* the reel hubs heat up like brake discs as the track plays (--heat 0..1) */
  .cassette .reel:before{content:"";position:absolute;inset:24%;border-radius:50%;
    background:radial-gradient(circle at 40% 35%,#3a3a44,#1c1c22);
    background:radial-gradient(circle at 40% 35%,color-mix(in srgb,#3a3a44,#ff3a00 calc(var(--heat,0)*88%)),color-mix(in srgb,#1c1c22,#7a1400 calc(var(--heat,0)*82%)));
    box-shadow:inset 0 0 0 2px #000,inset 0 0 calc(var(--heat,0)*16px) rgba(255,70,0,calc(var(--heat,0)*0.9)),0 0 calc(var(--heat,0)*30px) rgba(255,45,0,calc(var(--heat,0)*0.85));
    filter:brightness(calc(1 + var(--heat,0)*0.6));}
  .cassette .reel:after{content:"";position:absolute;left:50%;top:50%;width:14%;height:14%;margin:-7% 0 0 -7%;border-radius:50%;background:#000;z-index:2;}
  .cassette .tape{position:absolute;left:24%;right:24%;top:50%;height:3px;background:#42424c;}
  body.playing .cassette .reel{animation-play-state:running;}
  .gloss{position:absolute;left:14%;top:14%;width:72%;height:72%;border-radius:50%;pointer-events:none;z-index:2;
    background:linear-gradient(115deg,transparent 42%,rgba(255,255,255,.08) 50%,transparent 58%);
    animation:sheen 3.6s ease-in-out infinite;}
  @keyframes sheen{0%,100%{opacity:.35;}50%{opacity:.85;}}
  .fx{position:fixed;inset:0;pointer-events:none;z-index:5;
    background:radial-gradient(125% 85% at 50% 42%,transparent 52%,rgba(0,0,0,.5) 100%);}
  .fx::after{content:"";position:absolute;inset:0;opacity:.045;mix-blend-mode:overlay;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");}
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
</style></head>
<body>
<button class="reelbtn" id="skinbtn" style="right:96px">Cassette</button>
<button class="reelbtn" id="reelbtn">⤢ Reel</button>
<div class="fxbar" id="fxbar">
  <button class="fxchip" data-fx="smoke">Smoke</button>
  <button class="fxchip" data-fx="particles">Particles</button>
  <button class="fxchip" data-fx="shake">Shake</button>
  <button class="fxchip" data-fx="heat">Heat</button>
</div>
<div class="wrap">
  <h1>__PACK_NAME__</h1>
  <p class="by">Produced by <b>__PRODUCER__</b> · __MADE__ · __NBEATS__ beats</p>
  <div class="hook" id="hook"></div>
  <div class="deck" id="deck">
    <canvas id="viz"></canvas>
    <div class="vinyl" id="vinyl">__LABEL_HTML__<div class="hole"></div></div>
    <div class="gloss"></div>
    <div class="arm" id="arm"><i class="tip" id="armtip"></i></div>
    <div class="cassette" id="cassette">
      __CLABEL_HTML__
      <div class="win"><div class="reel"></div><div class="tape"></div><div class="reel"></div></div>
    </div>
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
<canvas class="pfx" id="pfx"></canvas>
<div class="fx"></div>
<div class="flash"></div>
<audio id="audio"></audio>
<script>
__VIZ_JS__
const PACK_COVER = __COVER_URL__;
const TRACKS = __TRACKS__;
const INQ = __INQUIRE_JSON__;  // {contact,producer,pack} — drives the per-beat Inquire button
function inquire(i){var t=TRACKS[i];  // mailto for now; the hosted build POSTs this to a tracked endpoint
  var subj='Beat inquiry: '+t.t+(INQ.pack?' ('+INQ.pack+')':'');
  var body='Hi'+(INQ.producer?' '+INQ.producer:'')+',\n\nI’m interested in "'+t.t+'"'+(INQ.pack?' from your '+INQ.pack+' pack':'')+'. Is it available?\n\n';
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
function setPlaying(p){document.body.classList.toggle('playing',p);arm.classList.toggle('on',p);playBtn.innerHTML=p?'&#10074;&#10074;':'&#9654;';}
function isCassette(){return document.body.classList.contains('cassette-mode');}
document.getElementById('skinbtn').onclick=function(){var on=document.body.classList.toggle('cassette-mode');
  this.textContent=on?'Vinyl':'Cassette';size();};
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
function centerOf(el){var r=el.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2};}
function smokeAt(){  // smoke rises from the hot reels (cassette) or the stylus (vinyl)
  if(isCassette()){return [].slice.call(document.querySelectorAll('#cassette .reel')).map(centerOf);}
  var t=document.getElementById('armtip');return t?[centerOf(t)]:[];}
ttRun({canvas:canvas,audio:audio,getAnalyser:function(){return analyser;},
  fxCanvas:document.getElementById('pfx'),smokeAt:smokeAt,
  scene:document.querySelector('.wrap'),
  getLabel:function(){return isCassette()?document.querySelector('#cassette .clabel'):document.querySelector('#vinyl .label');},
  flash:document.querySelector('.flash'),getCover:function(){return PACK_COVER;},
  getSkin:function(){return isCassette()?'cassette':'vinyl';},
  fxOn:function(n){return !document.body.classList.contains('off-'+n);},
  reelGet:function(){return document.body.classList.contains('reel');},sparksOn:true});
</script>
</body></html>
"""


def render_index_html(
    tracks: list[PackTrack], meta: PackMeta, filenames: list[str], cover: str | None = None
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
    }
    out = _PLAYER_TEMPLATE
    for token, value in subs.items():
        out = out.replace(token, value)
    return out


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def build_pack(
    tracks: list[PackTrack], out_dir: Path, meta: PackMeta, cover_src: Path | None = None,
    clean: bool = False, preview: bool = False, preview_seconds: int = 40,
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
        render_index_html(tracks, meta, filenames, cover=cover_name), encoding="utf-8"
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
