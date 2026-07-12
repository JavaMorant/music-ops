/* one-click reel export renderer (app-only). Draws the ENTIRE deck scene -
   background, reactive FX and the active skin (vinyl or cassette) - onto an
   offscreen 1080x1920 canvas driven by the live AnalyserNode. The app records
   that canvas with captureStream() + MediaRecorder: no screen-share prompt,
   always a perfect 9:16 frame. The visuals are a Canvas2D port of the deck's
   CSS look (deck/deck.css) and the turntable.js FX math (spectrum, halo,
   bokeh, embers, smoke, heat, kick reactions). The live DOM deck is never
   touched - this draws an independent copy at export resolution. */

function ttExportRender(cfg){
  'use strict';
  var FW = cfg.w || 1080, FH = cfg.h || 1920;
  var canvas = document.createElement('canvas');
  canvas.width = FW; canvas.height = FH;
  var ctx = canvas.getContext('2d');
  var PI = Math.PI, TAU = PI * 2;

  // composition: the deck box (the live .deck square) large + centred, a touch
  // above the middle so the spectrum, texts and risers breathe below it
  var D = Math.round(FW * 0.85);
  var cx = FW / 2, cyBase = Math.round(FH * 0.45);
  var ref = D;                 // FX reference length (live: deck width in fx px)
  var scL = D / 520;           // scales fixed CSS px authored at the live reel deck size
  var SHK = FW / 680;          // scales live screen-px shake to frame px

  var skin = cfg.skin === 'cassette' ? 'cassette' : 'vinyl';
  var producer = cfg.producer || 'Beats';
  var accent = cfg.accent || '#c9a227';
  var txtCol = cfg.txt || '#e8e4da', dimCol = cfg.dim || '#9a9aa2';
  var fontD = cfg.fontDisplay || 'Georgia,serif';
  var fontB = '-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
  var track = cfg.track || {name: '', hook: '', meta: ''};

  function on(n){ return !cfg.fxOn || cfg.fxOn(n); }

  // ---- colour helpers ----
  function parseCol(s){
    s = (s || '').trim();
    var m = /^#([0-9a-f]{3})$/i.exec(s);
    if(m) return [parseInt(m[1][0],16)*17, parseInt(m[1][1],16)*17, parseInt(m[1][2],16)*17];
    m = /^#([0-9a-f]{6})$/i.exec(s);
    if(m) return [parseInt(m[1].slice(0,2),16), parseInt(m[1].slice(2,4),16), parseInt(m[1].slice(4,6),16)];
    m = /^rgba?\(([^)]+)\)/i.exec(s);
    if(m){ var p = m[1].split(',').map(parseFloat); return [p[0]||0, p[1]||0, p[2]||0]; }
    return [201, 162, 39];
  }
  function mix(c1, c2, t){ var a = parseCol(c1), b = parseCol(c2);
    return 'rgb(' + Math.round(a[0]+(b[0]-a[0])*t) + ',' + Math.round(a[1]+(b[1]-a[1])*t) + ',' +
      Math.round(a[2]+(b[2]-a[2])*t) + ')'; }
  function lift(col, f){ var c = parseCol(col);
    return 'rgb(' + Math.min(255,Math.round(c[0]*f)) + ',' + Math.min(255,Math.round(c[1]*f)) + ',' +
      Math.min(255,Math.round(c[2]*f)) + ')'; }
  var accRgb = parseCol(accent);
  function accA(a){ return 'rgba(' + accRgb[0] + ',' + accRgb[1] + ',' + accRgb[2] + ',' + a + ')'; }

  // ---- path helpers ----
  function circle(c, x, y, r){ c.beginPath(); c.arc(x, y, Math.max(0, r), 0, TAU); }
  function rrect(c, x, y, w, h, r){ r = Math.min(r, w/2, h/2); c.beginPath();
    c.moveTo(x+r, y); c.arcTo(x+w, y, x+w, y+h, r); c.arcTo(x+w, y+h, x, y+h, r);
    c.arcTo(x, y+h, x, y, r); c.arcTo(x, y, x+w, y, r); c.closePath(); }
  function linGrad(c, x, y, w, h, deg, stops){  // CSS-style angle: 0deg = up, clockwise
    var a = deg*PI/180, dx = Math.sin(a), dy = -Math.cos(a);
    var L = Math.abs(w*dx) + Math.abs(h*dy), mx = x + w/2, my = y + h/2;
    var g = c.createLinearGradient(mx-dx*L/2, my-dy*L/2, mx+dx*L/2, my+dy*L/2);
    for(var i = 0; i < stops.length; i++) g.addColorStop(stops[i][0], stops[i][1]);
    return g; }
  function fitFont(c, weight, px, family, text, maxW){
    c.font = weight + ' ' + px + 'px ' + family;
    while(px > 16 && c.measureText(text).width > maxW){ px -= 2; c.font = weight + ' ' + px + 'px ' + family; }
    return px; }
  function coverInto(c, x, y, w, h){  // object-fit: cover
    var im = cfg.coverImg, s = Math.max(w/im.width, h/im.height);
    var dw = im.width*s, dh = im.height*s;
    c.drawImage(im, x+(w-dw)/2, y+(h-dh)/2, dw, dh); }

  // ---- reactive state (ported from turntable.js) ----
  var hue = 42, bassAvg = 0, eLong = 0, eMid = 0, shake = 0, punch = 0, lastDrop = 0;
  var idleT = 0, dataArr = null, curEnergy = 0, curProg = 0, curHeat = 0, heatVal = 0, breath = 0;
  var smoothV = null, playingNow = false, vinylPulse = 0;  // vinylPulse = eased kick level that makes the ring breathe
  var rot = 0, reelRot = 0, armA = 8;
  var sparks = [], embers = [], smoke = [], bokeh = [], dust = [];
  var running = false, raf = 0, lastNow = 0, startT = 0, endedAt = 0;

  // hue seeded from the cover art, like the live deck
  (function seedHue(){
    var im = cfg.coverImg; if(!im) return;
    try{
      var cc = document.createElement('canvas'); cc.width = cc.height = 14;
      var c2 = cc.getContext('2d'); c2.drawImage(im, 0, 0, 14, 14);
      var px = c2.getImageData(0, 0, 14, 14).data, r = 0, g = 0, b = 0, n = 0;
      for(var i = 0; i < px.length; i += 4){ if(px[i+3] > 10){ r += px[i]; g += px[i+1]; b += px[i+2]; n++; } }
      if(!n) return;
      r = r/n/255; g = g/n/255; b = b/n/255;
      var mx = Math.max(r,g,b), mn = Math.min(r,g,b), d = mx-mn, h = 0;
      if(d){ if(mx === r) h = ((g-b)/d+6)%6; else if(mx === g) h = (b-r)/d+2; else h = (r-g)/d+4; h *= 60; }
      hue = h;
    }catch(e){}
  })();

  // ---- prerendered sprites (static art drawn once, blitted per frame) ----
  var noiseSpr, bgSpr, vigSpr, scanSpr;
  var recSpr, lblSpr, glossSpr;                       // vinyl
  var casSpr, casLblSpr, casPad = 64, casLblW = 0, casLblH = 0;  // cassette
  var bokSpr = null, bokSprHue = -999;

  function buildNoise(){
    noiseSpr = document.createElement('canvas'); noiseSpr.width = noiseSpr.height = 160;
    var c = noiseSpr.getContext('2d'), id = c.createImageData(160, 160);
    for(var i = 0; i < id.data.length; i += 4){ var v = Math.floor(Math.random()*255);
      id.data[i] = v; id.data[i+1] = v; id.data[i+2] = v; id.data[i+3] = 255; }
    c.putImageData(id, 0, 0);
  }
  function buildBg(){  // #ov: radial-gradient(1100px 560px at 50% -8%, #1c1c24, #0b0b0e) + the faint grain
    bgSpr = document.createElement('canvas'); bgSpr.width = FW; bgSpr.height = FH;
    var b = bgSpr.getContext('2d');
    b.fillStyle = '#0b0b0e'; b.fillRect(0, 0, FW, FH);
    b.save(); b.translate(FW/2, -0.08*FH); b.scale(1, (FH*0.42)/(FW*1.05));
    var g = b.createRadialGradient(0, 0, 0, 0, 0, FW*1.05);
    g.addColorStop(0, '#1c1c24'); g.addColorStop(1, '#0b0b0e');
    b.fillStyle = g; b.fillRect(-4*FW, -4*FW, 8*FW, 12*FW);
    b.restore();
    b.globalAlpha = 0.045; b.globalCompositeOperation = 'overlay';
    b.fillStyle = b.createPattern(noiseSpr, 'repeat'); b.fillRect(0, 0, FW, FH);
    b.globalAlpha = 1; b.globalCompositeOperation = 'source-over';
  }
  function buildVig(){  // radial-gradient(120% 88% at 50% 44%, transparent 54%, rgba(0,0,0,.42))
    vigSpr = document.createElement('canvas'); vigSpr.width = FW; vigSpr.height = FH;
    var v = vigSpr.getContext('2d');
    v.save(); v.translate(FW/2, 0.44*FH); v.scale(1, (0.88*FH)/(1.2*FW));
    var g = v.createRadialGradient(0, 0, 0, 0, 0, 1.2*FW);
    g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(0.54, 'rgba(0,0,0,0)'); g.addColorStop(1, 'rgba(0,0,0,.42)');
    v.fillStyle = g; v.fillRect(-3*FW, -6*FW, 6*FW, 12*FW);
    v.restore();
  }
  function buildScan(){  // VHS scanlines tile (dark 2px / clear 4px)
    scanSpr = document.createElement('canvas'); scanSpr.width = 4; scanSpr.height = 6;
    var c = scanSpr.getContext('2d');
    c.fillStyle = 'rgba(0,0,0,.14)'; c.fillRect(0, 0, 4, 2);
  }

  function buildRecord(){  // the full record minus the label: grooves, ring gaps, rim, streaks, ring text
    var Rv = D*0.36, S = Math.ceil(Rv*2) + 140, cs = S/2;
    recSpr = document.createElement('canvas'); recSpr.width = recSpr.height = S;
    var c = recSpr.getContext('2d');
    c.save(); c.shadowColor = 'rgba(0,0,0,.6)'; c.shadowBlur = 46*scL*0.6; c.shadowOffsetY = 10*scL;
    c.fillStyle = '#0c0c0e'; circle(c, cs, cs, Rv); c.fill(); c.restore();
    // grooves: repeating fine rings (#0c0c0e base / #191920 lines)
    c.strokeStyle = '#191920'; c.lineWidth = Rv*0.0085;
    for(var r = Rv*0.01275; r < Rv*0.97; r += Rv*0.017){ circle(c, cs, cs, r); c.stroke(); }
    // ring band structure from deck.css
    function band(a, b2, col){ c.strokeStyle = col; c.lineWidth = (b2-a)*Rv; circle(c, cs, cs, (a+b2)/2*Rv); c.stroke(); }
    band(0.405, 0.46, '#101014');
    band(0.575, 0.586, 'rgba(0,0,0,.42)');
    band(0.695, 0.706, 'rgba(0,0,0,.42)');
    band(0.805, 0.816, 'rgba(0,0,0,.42)');
    band(0.972, 0.979, 'rgba(255,255,255,.13)');
    band(0.979, 1.0, '#000');
    // faint light streaks pressed into the disc (rotate with it, like the CSS conic layer)
    if(c.createConicGradient){
      var cg = c.createConicGradient(-PI/2, cs, cs);
      cg.addColorStop(0, 'rgba(255,255,255,0)'); cg.addColorStop(30/360, 'rgba(255,255,255,.05)');
      cg.addColorStop(72/360, 'rgba(255,255,255,0)'); cg.addColorStop(205/360, 'rgba(255,255,255,0)');
      cg.addColorStop(235/360, 'rgba(255,255,255,.035)'); cg.addColorStop(280/360, 'rgba(255,255,255,0)');
      cg.addColorStop(1, 'rgba(255,255,255,0)');
      c.fillStyle = cg; circle(c, cs, cs, Rv); c.fill();
    }
    c.lineWidth = 2*scL; c.strokeStyle = '#000'; circle(c, cs, cs, Rv - scL); c.stroke();
    // pressed ring text just outside the label
    var text = '45 RPM \u00b7 STEREO \u00b7 LONG PLAY \u00b7 45 RPM \u00b7 STEREO \u00b7';
    var fs = Rv*0.068, rr2 = Rv*0.47;
    c.save(); c.fillStyle = 'rgba(255,255,255,.3)'; c.font = '600 ' + fs.toFixed(0) + 'px ' + fontD;
    c.textAlign = 'center'; c.textBaseline = 'middle';
    var a = -PI/2;
    for(var i = 0; i < text.length; i++){
      var w = c.measureText(text[i]).width + fs*0.18, da = w/rr2;
      a += da/2;
      c.save(); c.translate(cs + Math.cos(a)*rr2, cs + Math.sin(a)*rr2); c.rotate(a + PI/2);
      c.fillText(text[i], 0, 0); c.restore();
      a += da/2;
    }
    c.restore();
  }
  function buildLabel(){  // brass label with cover art (or the producer wordmark)
    var Rl = D*0.144, S = Math.ceil(Rl*2) + Math.ceil(8*scL) + 8, cs = S/2;
    lblSpr = document.createElement('canvas'); lblSpr.width = lblSpr.height = S;
    var c = lblSpr.getContext('2d');
    c.save(); circle(c, cs, cs, Rl); c.clip();
    if(cfg.coverImg){
      c.fillStyle = '#000'; c.fillRect(0, 0, S, S);
      coverInto(c, cs-Rl, cs-Rl, Rl*2, Rl*2);
    } else {
      var g = c.createRadialGradient(cs, cs - Rl*0.32, 0, cs, cs - Rl*0.32, Rl*1.7);
      g.addColorStop(0, accent); g.addColorStop(1, '#8a6f17');
      c.fillStyle = g; c.fillRect(0, 0, S, S);
      c.strokeStyle = 'rgba(0,0,0,.06)'; c.lineWidth = Rl*0.07;
      for(var r = Rl*0.07; r < Rl; r += Rl*0.14){ circle(c, cs, cs, r); c.stroke(); }
      c.fillStyle = '#1a1405'; c.textAlign = 'center'; c.textBaseline = 'middle';
      var name = (producer || '').toUpperCase();
      fitFont(c, '800', Math.round(Rl*0.34), fontB, name, Rl*1.5);
      c.fillText(name, cs, cs);
    }
    c.restore();
    c.lineWidth = 2.5*scL; c.strokeStyle = 'rgba(0,0,0,.25)'; circle(c, cs, cs, Rl - 1.2*scL); c.stroke();
    c.lineWidth = 3*scL; c.strokeStyle = 'rgba(255,255,255,.1)'; circle(c, cs, cs, Rl + 1.5*scL); c.stroke();
  }
  function buildGloss(){  // fixed room-light: two soft specular bands + a linear sheen
    var Rv = D*0.36, S = Math.ceil(Rv*2) + 4, cs = S/2;
    glossSpr = document.createElement('canvas'); glossSpr.width = glossSpr.height = S;
    var c = glossSpr.getContext('2d');
    if(c.createConicGradient){
      var cg = c.createConicGradient(-PI/2, cs, cs);
      cg.addColorStop(0, 'rgba(255,255,255,0)'); cg.addColorStop(18/360, 'rgba(255,255,255,0)');
      cg.addColorStop(38/360, 'rgba(255,255,255,.13)'); cg.addColorStop(58/360, 'rgba(255,255,255,0)');
      cg.addColorStop(196/360, 'rgba(255,255,255,0)'); cg.addColorStop(218/360, 'rgba(255,255,255,.09)');
      cg.addColorStop(240/360, 'rgba(255,255,255,0)'); cg.addColorStop(1, 'rgba(255,255,255,0)');
      c.fillStyle = cg; circle(c, cs, cs, Rv); c.fill();
    }
    c.save(); circle(c, cs, cs, Rv); c.clip();
    c.fillStyle = linGrad(c, 0, 0, S, S, 115, [
      [0.44, 'rgba(255,255,255,0)'], [0.50, 'rgba(255,255,255,.05)'], [0.56, 'rgba(255,255,255,0)']]);
    c.fillRect(0, 0, S, S);
    c.restore();
  }

  function buildCassette(){  // charcoal shell + window + tape (label + reels drawn live)
    var shW = 0.88*D, shH = 0.52*D;
    casSpr = document.createElement('canvas');
    casSpr.width = Math.ceil(shW) + casPad*2; casSpr.height = Math.ceil(shH) + casPad*2;
    var c = casSpr.getContext('2d'), x = casPad, y = casPad, rad = 14*scL;
    c.save(); c.shadowColor = 'rgba(0,0,0,.6)'; c.shadowBlur = 46*scL*0.6; c.shadowOffsetY = 10*scL;
    c.fillStyle = linGrad(c, x, y, shW, shH, 165, [[0, '#34343f'], [1, '#16161c']]);
    rrect(c, x, y, shW, shH, rad); c.fill(); c.restore();
    c.lineWidth = 1.6*scL; c.strokeStyle = '#000'; rrect(c, x, y, shW, shH, rad); c.stroke();
    c.lineWidth = 2*scL; c.strokeStyle = 'rgba(255,255,255,.05)';
    rrect(c, x + 2*scL, y + 2*scL, shW - 4*scL, shH - 4*scL, rad*0.86); c.stroke();
    // window
    var wX = x + 0.11*shW, wW = 0.78*shW, wY = y + 0.37*shH, wH = 0.48*shH, wr = 10*scL;
    c.fillStyle = '#0b0b0f'; rrect(c, wX, wY, wW, wH, wr); c.fill();
    c.save(); rrect(c, wX, wY, wW, wH, wr); c.clip();
    c.fillStyle = linGrad(c, wX, wY, wW, wH, 180, [
      [0, 'rgba(0,0,0,.7)'], [0.22, 'rgba(0,0,0,0)'], [1, 'rgba(0,0,0,0)']]);
    c.fillRect(wX, wY, wW, wH); c.restore();
    c.lineWidth = 2*scL; c.strokeStyle = '#000'; rrect(c, wX, wY, wW, wH, wr); c.stroke();
    // tape strip
    c.fillStyle = '#42424c';
    c.fillRect(x + 0.24*shW, y + 0.5*shH - 2.5*scL, 0.52*shW, 5*scL);
  }
  function buildCasLabel(){  // gold/brass label (or cover art) on a rounded card
    var shW = 0.88*D, shH = 0.52*D;
    casLblW = 0.82*shW; casLblH = 0.26*shH;
    casLblSpr = document.createElement('canvas');
    casLblSpr.width = Math.ceil(casLblW) + 4; casLblSpr.height = Math.ceil(casLblH) + 4;
    var c = casLblSpr.getContext('2d'), x = 2, y = 2, rad = 6*scL;
    c.save(); rrect(c, x, y, casLblW, casLblH, rad); c.clip();
    if(cfg.coverImg){ coverInto(c, x, y, casLblW, casLblH); }
    else {
      var g = c.createRadialGradient(x + casLblW/2, y + casLblH*0.3, 0, x + casLblW/2, y + casLblH*0.3, casLblW*0.62);
      g.addColorStop(0, accent); g.addColorStop(1, '#8a6f17');
      c.fillStyle = g; c.fillRect(x, y, casLblW, casLblH);
      c.fillStyle = '#1a1405'; c.textAlign = 'center'; c.textBaseline = 'middle';
      var name = (producer || '').toUpperCase();
      fitFont(c, '800', Math.round(casLblH*0.42), fontB, name, casLblW*0.86);
      c.fillText(name, x + casLblW/2, y + casLblH/2);
    }
    c.restore();
    c.lineWidth = 2*scL; c.strokeStyle = 'rgba(0,0,0,.2)';
    rrect(c, x + scL, y + scL, casLblW - 2*scL, casLblH - 2*scL, rad*0.8); c.stroke();
  }
  function bokehSprite(){  // one soft orb, re-tinted only when the drifting hue moves
    if(!bokSpr){ bokSpr = document.createElement('canvas'); bokSpr.width = bokSpr.height = 128; }
    var rh = Math.round(hue/6)*6;
    if(rh !== bokSprHue){ bokSprHue = rh; var c = bokSpr.getContext('2d'); c.clearRect(0, 0, 128, 128);
      var g = c.createRadialGradient(64, 64, 0, 64, 64, 64);
      g.addColorStop(0, 'hsla(' + rh + ',90%,72%,1)');
      g.addColorStop(0.4, 'hsla(' + rh + ',88%,62%,0.4)');
      g.addColorStop(1, 'hsla(' + rh + ',88%,60%,0)');
      c.fillStyle = g; circle(c, 64, 64, 64); c.fill(); }
    return bokSpr;
  }

  buildNoise(); buildBg(); buildVig(); buildScan();
  if(skin === 'cassette'){ buildCassette(); buildCasLabel(); }
  else { buildRecord(); buildLabel(); buildGloss(); }

  // ---- geometry ----
  function armGeom(cyd){
    var dxx = cx - D/2, dyy = cyd - D/2;
    return {pivX: dxx + 0.96*D, pivY: dyy + 0.036*D, armW: 0.46*D, armH: 0.032*D, a: armA*PI/180};
  }
  function armTip(cyd){ var g = armGeom(cyd);
    return {x: g.pivX - g.armW*Math.cos(g.a), y: g.pivY - g.armW*Math.sin(g.a)}; }
  function casGeom(cyd){
    var dxx = cx - D/2, dyy = cyd - D/2;
    var shX = dxx + 0.06*D, shY = dyy + 0.24*D, shW = 0.88*D, shH = 0.52*D;
    var wX = shX + 0.11*shW, wW = 0.78*shW, wY = shY + 0.37*shH, wH = 0.48*shH;
    var rd = 0.31*(wW*0.82)/2, wyc = wY + wH/2;
    return {shX: shX, shY: shY, shW: shW, shH: shH,
      lC: {x: wX + 0.09*wW + rd, y: wyc}, rC: {x: wX + 0.91*wW - rd, y: wyc}, rd: rd};
  }
  function smokePts(cyd){
    if(skin === 'cassette'){ var g = casGeom(cyd); return [g.lC, g.rC]; }
    return [armTip(cyd)];
  }

  // ---- particle spawners (turntable.js math at export resolution) ----
  function ambient(energy){ var cap = 10 + Math.floor(energy*60), n = 1 + Math.floor(energy*4);
    for(var q = 0; q < n; q++){ if(embers.length < cap && Math.random() < 0.5){
      embers.push({x: Math.random()*FW, y: FH + 8, vx: (Math.random()*2-1)*FW*0.0004,
        vy: -(FH*0.0011)*(0.5 + Math.random()*1.4)*(0.6 + energy*1.3), life: 1,
        sz: ref*(0.004 + Math.random()*0.01)}); } } }
  function dustSpawn(){ if(dust.length >= 36 || Math.random() > 0.3) return;
    dust.push({x: Math.random()*FW, y: Math.random()*FH, vx: (Math.random()*2-1)*FW*0.00008,
      vy: -(FH*0.00012)*(0.4 + Math.random()), t: 0, dt: 0.0016 + Math.random()*0.002,
      sz: ref*(0.0016 + Math.random()*0.0034), ph: Math.random()*TAU, tw: 2 + Math.random()*3}); }
  function bokehSpawn(energy){ var cap = 46 + Math.floor(energy*100), n = 1 + Math.floor(energy*3);
    for(var q = 0; q < n; q++){ if(bokeh.length < cap && Math.random() < 0.7){
      var z = Math.random();
      bokeh.push({x: Math.random()*FW, y: FH*(0.15 + Math.random()*1.05),
        vx: (Math.random()*2-1)*FW*0.00012*(0.35 + z*1.7),
        vy: -(FH*0.0004)*(0.4 + z*1.9)*(0.5 + Math.random()*0.8),
        t: 0, dt: 0.0024 + Math.random()*0.0038, sz: ref*(0.006 + z*z*0.052),
        ph: Math.random()*TAU, amp: ref*(0.035 + z*0.17), z: z,
        peak: (0.1 + Math.random()*0.16)*(1 - z*0.45) + energy*0.08}); } } }
  function puff(x, y, heat){ if(smoke.length > 120) return;
    smoke.push({bx: x, x: x, y: y, vy: -(FH*0.0012)*(0.8 + Math.random()*0.5),
      r: ref*0.003, life: 1, heat: heat, ph: Math.random()*TAU, amp: ref*(0.01 + Math.random()*0.016)}); }
  function spawnSparks(k){ var n = Math.min(14, Math.floor(k*26) + Math.floor(curEnergy*10)), cyd = cyBase;
    for(var j = 0; j < n; j++){ var a = Math.random()*TAU, sp = FW*0.004*(1 + Math.random()*3);
      sparks.push({x: cx + Math.cos(a)*ref*0.36, y: cyd + Math.sin(a)*ref*0.36,
        vx: Math.cos(a)*sp, vy: Math.sin(a)*sp, life: 1, h: (hue + Math.random()*50)%360}); } }

  // ---- scene pieces ----
  function drawHalo(cyd, energy){
    var R0 = D*0.36, halo = 0.05 + energy*0.25 + breath*0.03;
    var g = ctx.createRadialGradient(cx, cyd, R0*0.55, cx, cyd, R0*(1.1 + energy*0.45 + breath*0.05));
    g.addColorStop(0, 'hsla(' + hue.toFixed(0) + ',90%,55%,' + halo.toFixed(3) + ')');
    g.addColorStop(1, 'hsla(' + hue.toFixed(0) + ',90%,55%,0)');
    ctx.fillStyle = g; circle(ctx, cx, cyd, R0*1.35); ctx.fill();
  }
  function drawSpectrum(cyd, kick){
    var cassette = skin === 'cassette';
    var R0 = D*0.36;
    var dxx = cx - D/2, dyy = cyd - D/2;
    if(!cassette && kick > 0.02){ ctx.save(); ctx.globalAlpha = Math.min(0.9, kick*3.5);
      ctx.strokeStyle = 'hsl(' + hue.toFixed(0) + ',95%,72%)'; ctx.lineWidth = D*0.006;
      circle(ctx, cx, cyd, R0*1.03); ctx.stroke(); ctx.restore(); }
    var bars = cassette ? 80 : 96, half = bars/2, envAmp = playingNow ? 0.05 : 0.14;
    if(!smoothV || smoothV.length !== bars) smoothV = new Float32Array(bars);
    ctx.save(); ctx.shadowBlur = D*0.006; ctx.lineCap = 'round';
    if(cassette){
      var baseY = dyy + D*0.885, ctw = D*0.82, clx = dxx + D*0.09; ctx.lineWidth = D*0.012;
      var prc = curProg;
      for(var i = 0; i < bars; i++){ var idx = i < half ? i : bars-1-i;
        var raw = dataArr ? dataArr[Math.floor(idx/half*dataArr.length*0.7)]/255 : 0;
        var env = envAmp*(0.5 + 0.5*Math.sin(idleT*1.7 + idx*0.5));
        smoothV[i] += (Math.max(raw, env) - smoothV[i])*0.35; var v = smoothV[i];
        var fxp = i/(bars-1), x = clx + fxp*ctw, len = D*0.012 + v*v*D*0.16, played = fxp <= prc;
        var col = played
          ? 'hsl(' + ((hue + idx/half*46)%360).toFixed(0) + ',' + (80 + v*20).toFixed(0) + '%,' + (56 + v*20).toFixed(0) + '%)'
          : 'hsla(40,12%,' + (42 + v*16).toFixed(0) + '%,.55)';
        ctx.strokeStyle = col; ctx.shadowColor = played ? col : 'transparent';
        ctx.beginPath(); ctx.moveTo(x, baseY - len/2); ctx.lineTo(x, baseY + len/2); ctx.stroke(); }
    } else {
      // Trap-Nation ring (kept in lockstep with turntable.js): bass anchored at the
      // BOTTOM centre (+PI/2, canvas y-down), frequency climbing up BOTH sides
      // (mirrored across the vertical axis) to treble at the TOP. Snappy attack with
      // a slower meter-style fall, a perceptual (pow) bin map so the bass owns the
      // bottom arc, kick-driven bass bars, level-scaled glow, white-hot peak tips,
      // and the whole ring breathing outward on the beat.
      ctx.lineWidth = D*0.013;
      vinylPulse = Math.max(vinylPulse*0.92, Math.min(1, kick*4));
      var R0v = R0*(1 + vinylPulse*0.022), maxV = D*0.483;
      ctx.shadowBlur = D*(0.005 + Math.min(0.012, (curEnergy*0.8 + vinylPulse*0.5)*0.014));
      for(var b = 0; b < bars; b++){ var j2 = b < half ? b : bars-1-b, fp = j2/(half-1);
        var raw2 = dataArr ? Math.min(1, dataArr[Math.floor(Math.pow(fp, 1.7)*dataArr.length*0.5)]/255*(1 + fp*0.55)) : 0;
        var env2 = envAmp*(0.5 + 0.5*Math.sin(idleT*1.7 + j2*0.5)), tg2 = Math.max(raw2, env2);
        smoothV[b] += (tg2 - smoothV[b])*(tg2 > smoothV[b] ? 0.65 : 0.18); var w2 = smoothV[b];
        var l2 = Math.min(D*0.010 + Math.pow(w2, 1.5)*D*0.155 + kick*(1 - fp)*D*0.07, maxV - R0v);
        var a2 = b/bars*TAU + PI/2, c2 = Math.cos(a2), s2 = Math.sin(a2);
        var hb = ((hue + fp*40)%360).toFixed(0);
        var k2 = 'hsl(' + hb + ',' + (70 + w2*30).toFixed(0) + '%,' + (50 + w2*26).toFixed(0) + '%)';
        ctx.strokeStyle = k2; ctx.shadowColor = k2;
        ctx.beginPath(); ctx.moveTo(cx + c2*R0v, cyd + s2*R0v); ctx.lineTo(cx + c2*(R0v + l2), cyd + s2*(R0v + l2)); ctx.stroke();
        if(w2 > 0.7){ ctx.save(); ctx.globalAlpha = Math.min(1, (w2 - 0.7)*3)*0.85;
          var kw = 'hsl(' + hb + ',100%,88%)';
          ctx.strokeStyle = kw; ctx.shadowColor = kw; ctx.lineWidth = D*0.008;
          ctx.beginPath(); ctx.moveTo(cx + c2*(R0v + l2*0.55), cyd + s2*(R0v + l2*0.55)); ctx.lineTo(cx + c2*(R0v + l2), cyd + s2*(R0v + l2)); ctx.stroke(); ctx.restore(); } }
    }
    ctx.restore();
  }
  function drawArm(cyd){
    var g = armGeom(cyd);
    ctx.save(); ctx.translate(g.pivX, g.pivY); ctx.rotate(g.a);
    ctx.shadowColor = 'rgba(0,0,0,.5)'; ctx.shadowBlur = 7*scL; ctx.shadowOffsetY = 2*scL;
    ctx.fillStyle = linGrad(ctx, -g.armW, -g.armH/2, g.armW, g.armH, 180, [[0, '#60606c'], [1, '#2a2a32']]);
    rrect(ctx, -g.armW, -g.armH/2, g.armW, g.armH, 4*scL); ctx.fill();
    ctx.shadowColor = 'transparent'; ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
    // headshell at the record end, angled like the live .arm:after
    ctx.save(); ctx.translate(-g.armW*0.98, -g.armH*0.2); ctx.rotate(24*PI/180);
    ctx.fillStyle = linGrad(ctx, -g.armW*0.06, 0, g.armW*0.12, g.armH*2.4, 180, [[0, '#3a3a44'], [1, '#191920']]);
    rrect(ctx, -g.armW*0.06, 0, g.armW*0.12, g.armH*2.4, 2*scL); ctx.fill();
    ctx.lineWidth = scL; ctx.strokeStyle = '#000'; ctx.stroke();
    ctx.restore();
    // counterweight over the pivot
    var cwR = g.armW*0.13;
    var cg = ctx.createRadialGradient(-cwR*0.25, -cwR*0.35, cwR*0.1, 0, 0, cwR);
    cg.addColorStop(0, '#52525e'); cg.addColorStop(1, '#1c1c22');
    circle(ctx, -g.armW*0.01, 0, cwR);
    ctx.fillStyle = cg; ctx.fill(); ctx.lineWidth = scL; ctx.strokeStyle = '#000'; ctx.stroke();
    ctx.restore();
  }
  function drawVinyl(cyd, kick){
    ctx.save(); ctx.translate(cx, cyd); ctx.rotate(rot);
    ctx.drawImage(recSpr, -recSpr.width/2, -recSpr.height/2);
    ctx.restore();
    var ls = 1 + Math.min(0.2, kick*1.4) + punch*0.06 + (playingNow ? 0 : breath*0.02);
    ctx.save(); ctx.translate(cx, cyd); ctx.rotate(rot); ctx.scale(ls, ls);
    ctx.drawImage(lblSpr, -lblSpr.width/2, -lblSpr.height/2);
    ctx.restore();
    var hr = D*0.018;
    circle(ctx, cx, cyd, hr); ctx.fillStyle = '#000'; ctx.fill();
    ctx.lineWidth = 6*scL; ctx.strokeStyle = '#b6911f'; ctx.stroke();
    ctx.save(); ctx.globalAlpha = 0.55 + 0.45*(0.5 + 0.5*Math.sin(idleT*TAU/5.2));
    ctx.drawImage(glossSpr, cx - glossSpr.width/2, cyd - glossSpr.height/2); ctx.restore();
    drawArm(cyd);
  }
  function drawReel(x, y, rd, hv){
    ctx.save(); ctx.translate(x, y); ctx.rotate(reelRot);
    var step = TAU/20;
    for(var i = 0; i < 20; i++){ ctx.fillStyle = (i%2) ? '#14141a' : '#34343e';
      ctx.beginPath(); ctx.moveTo(0, 0); ctx.arc(0, 0, rd, i*step, (i+1)*step); ctx.closePath(); ctx.fill(); }
    ctx.restore();
    ctx.lineWidth = 3*scL; ctx.strokeStyle = '#000'; circle(ctx, x, y, rd - 1.5*scL); ctx.stroke();
    // hub heats up like a brake disc (--heat)
    var hr = rd*0.52, f = 1 + hv*0.6;
    var rg = ctx.createRadialGradient(x - hr*0.2, y - hr*0.3, hr*0.1, x, y, hr);
    rg.addColorStop(0, lift(mix('#3a3a44', '#ff3a00', hv*0.88), f));
    rg.addColorStop(1, lift(mix('#1c1c22', '#7a1400', hv*0.82), f));
    ctx.save();
    if(hv > 0.02){ ctx.shadowColor = 'rgba(255,45,0,' + (hv*0.85).toFixed(3) + ')'; ctx.shadowBlur = 30*scL*hv; }
    ctx.fillStyle = rg; circle(ctx, x, y, hr); ctx.fill(); ctx.restore();
    ctx.lineWidth = 2*scL; ctx.strokeStyle = '#000'; circle(ctx, x, y, hr); ctx.stroke();
    if(hv > 0.02){ ctx.save(); ctx.globalAlpha = Math.min(0.9, hv*0.9);
      ctx.strokeStyle = 'rgba(255,70,0,.8)'; ctx.lineWidth = 4*scL;
      ctx.shadowColor = 'rgba(255,70,0,.9)'; ctx.shadowBlur = 16*scL*hv;
      circle(ctx, x, y, hr*0.72); ctx.stroke(); ctx.restore(); }
    ctx.fillStyle = '#000'; circle(ctx, x, y, rd*0.07); ctx.fill();
  }
  function drawCassette(cyd, kick){
    var g = casGeom(cyd);
    ctx.drawImage(casSpr, g.shX - casPad, g.shY - casPad);
    var ls = 1 + Math.min(0.2, kick*1.4) + punch*0.06 + (playingNow ? 0 : breath*0.02);
    var lx = g.shX + 0.09*g.shW + casLblW/2, ly = g.shY + 0.08*g.shH + casLblH/2;
    ctx.save(); ctx.translate(lx, ly); ctx.scale(ls, ls);
    ctx.drawImage(casLblSpr, -casLblSpr.width/2, -casLblSpr.height/2); ctx.restore();
    var hv = on('heat') ? heatVal : 0;
    drawReel(g.lC.x, g.lC.y, g.rd, hv);
    drawReel(g.rC.x, g.rC.y, g.rd, hv);
  }
  function drawTrail(cyd){  // the stylus scorches a red groove as heat builds (vinyl only)
    if(skin === 'cassette' || !on('heat') || curHeat <= 0.001) return;
    var h = curHeat, tip = armTip(cyd);
    var contactR = Math.sqrt((tip.x-cx)*(tip.x-cx) + (tip.y-cyd)*(tip.y-cyd));
    contactR = Math.max(ref*0.2, Math.min(contactR, ref*0.36));
    var outerR = ref*0.36;
    if(outerR - contactR > 1){ ctx.save(); ctx.globalAlpha = Math.min(0.45, h*0.45);
      ctx.strokeStyle = 'rgba(42,10,4,1)'; ctx.lineWidth = outerR - contactR;
      circle(ctx, cx, cyd, (outerR + contactR)/2); ctx.stroke(); ctx.restore(); }
    ctx.save(); ctx.globalAlpha = Math.min(0.85, h*0.85);
    ctx.shadowBlur = ref*0.05*h; ctx.shadowColor = 'rgba(255,60,0,1)';
    ctx.strokeStyle = 'hsl(' + (16 + h*8).toFixed(0) + ',100%,' + (46 + h*16).toFixed(0) + '%)';
    ctx.lineWidth = ref*0.012*(0.6 + h);
    circle(ctx, cx, cyd, contactR); ctx.stroke(); ctx.restore();
    var hr2 = ref*0.12*(0.45 + h);
    var hg = ctx.createRadialGradient(tip.x, tip.y, 0, tip.x, tip.y, hr2);
    hg.addColorStop(0, 'rgba(255,232,190,' + Math.min(0.95, 0.35 + h*0.6).toFixed(3) + ')');
    hg.addColorStop(0.4, 'rgba(255,80,0,' + Math.min(0.85, h*0.85).toFixed(3) + ')');
    hg.addColorStop(1, 'rgba(255,40,0,0)');
    ctx.fillStyle = hg; circle(ctx, tip.x, tip.y, hr2); ctx.fill(); ctx.globalAlpha = 1;
  }
  function drawFX(cyd){  // particles above the deck, like the live full-screen pfx canvas
    drawTrail(cyd);
    var i;
    if(dust.length){ ctx.save();
      for(i = dust.length-1; i >= 0; i--){ var d = dust[i]; d.t += d.dt;
        if(d.t >= 1){ dust.splice(i, 1); continue; }
        d.x += d.vx + Math.sin(d.t*TAU*d.tw + d.ph)*FW*0.00018; d.y += d.vy;
        var da = Math.sin(d.t*PI)*(0.1 + 0.16*Math.sin(d.t*TAU*d.tw*1.7 + d.ph));
        if(da <= 0) continue;
        ctx.globalAlpha = Math.min(0.3, da); ctx.fillStyle = 'rgba(255,248,235,1)';
        circle(ctx, d.x, d.y, d.sz); ctx.fill(); }
      ctx.restore(); ctx.globalAlpha = 1; }
    if(bokeh.length){ var spr = bokehSprite(); ctx.save(); ctx.globalCompositeOperation = 'lighter';
      for(i = bokeh.length-1; i >= 0; i--){ var bo = bokeh[i]; bo.t += bo.dt;
        if(bo.t >= 1){ bokeh.splice(i, 1); continue; }
        bo.y += bo.vy; bo.x += bo.vx + Math.sin(bo.t*TAU + bo.ph)*bo.amp*0.012;
        var ba = Math.sin(bo.t*PI)*bo.peak;
        if(ba <= 0) continue;
        ctx.globalAlpha = Math.min(1, ba);
        ctx.drawImage(spr, bo.x - bo.sz, bo.y - bo.sz, bo.sz*2, bo.sz*2); }
      ctx.restore(); ctx.globalAlpha = 1; }
    for(i = smoke.length-1; i >= 0; i--){ var pf = smoke[i]; pf.y += pf.vy; pf.vy *= 0.997;
      var age = 1 - pf.life; pf.x = pf.bx + Math.sin(age*9 + pf.ph)*pf.amp*age;
      pf.r = ref*0.003 + age*ref*0.013; pf.life -= 0.006;
      if(pf.life <= 0){ smoke.splice(i, 1); continue; }
      var sr = Math.floor(120 + pf.heat*135), sg = Math.floor(120 - pf.heat*72), sb = Math.floor(120 - pf.heat*96);
      var gg = ctx.createRadialGradient(pf.x, pf.y, 0, pf.x, pf.y, pf.r);
      gg.addColorStop(0, 'rgba(' + sr + ',' + sg + ',' + sb + ',' + (pf.life*0.2).toFixed(3) + ')');
      gg.addColorStop(1, 'rgba(' + sr + ',' + sg + ',' + sb + ',0)');
      ctx.fillStyle = gg; circle(ctx, pf.x, pf.y, pf.r); ctx.fill(); }
    ctx.globalAlpha = 1;
    for(i = embers.length-1; i >= 0; i--){ var p = embers[i]; p.x += p.vx; p.y += p.vy; p.life -= 0.004;
      if(p.y < -14 || p.life <= 0){ embers.splice(i, 1); continue; }
      ctx.globalAlpha = p.life*0.4; ctx.fillStyle = 'hsl(' + hue.toFixed(0) + ',70%,62%)';
      circle(ctx, p.x, p.y, p.sz); ctx.fill(); }
    ctx.globalAlpha = 1;
    for(i = sparks.length-1; i >= 0; i--){ var sp = sparks[i];
      sp.x += sp.vx; sp.y += sp.vy; sp.vx *= 0.975; sp.vy *= 0.975; sp.life -= 0.02;
      if(sp.life <= 0){ sparks.splice(i, 1); continue; }
      ctx.globalAlpha = Math.max(0, sp.life); ctx.fillStyle = 'hsl(' + sp.h.toFixed(0) + ',95%,66%)';
      circle(ctx, sp.x, sp.y, ref*0.008); ctx.fill(); }
    ctx.globalAlpha = 1;
  }
  var vhsOffX = 0, vhsOffY = 0, vhsStep = -1;
  function drawVHS(now){  // animated grain + scanlines + the drifting tracking line
    ctx.save(); ctx.globalAlpha = 0.16; ctx.globalCompositeOperation = 'overlay';
    ctx.fillStyle = ctx.createPattern(scanSpr, 'repeat'); ctx.fillRect(0, 0, FW, FH);
    var stp = Math.floor(now/150);
    if(stp !== vhsStep){ vhsStep = stp; vhsOffX = Math.floor(Math.random()*160); vhsOffY = Math.floor(Math.random()*160); }
    ctx.translate(-vhsOffX, -vhsOffY);
    ctx.fillStyle = ctx.createPattern(noiseSpr, 'repeat'); ctx.fillRect(0, 0, FW + 160, FH + 160);
    ctx.restore();
    var p = (now%6500)/6500;
    if(p < 0.10){ var ty = -0.06*FH + (p/0.10)*1.10*FH;
      ctx.save(); ctx.globalAlpha = p < 0.015 ? (p/0.015)*0.5 : 0.35;
      var lg = ctx.createLinearGradient(0, 0, FW, 0);
      lg.addColorStop(0, 'rgba(255,255,255,0)'); lg.addColorStop(0.18, 'rgba(255,255,255,.5)');
      lg.addColorStop(0.82, 'rgba(255,255,255,.5)'); lg.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = lg; ctx.shadowColor = 'rgba(150,220,255,.4)'; ctx.shadowBlur = 14;
      ctx.fillRect(0, ty, FW, 5); ctx.restore(); }
  }
  function drawTexts(cyd){
    var dyy = cyd - D/2;
    ctx.save(); ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
    if(track.hook){
      try{ ctx.letterSpacing = '2px'; }catch(e){}
      ctx.font = '800 ' + Math.round(FW*0.038) + 'px ' + fontB;
      ctx.shadowColor = accA(0.4); ctx.shadowBlur = 28;
      ctx.fillStyle = accent;
      ctx.fillText(track.hook.toUpperCase(), cx, dyy - FW*0.03);
      ctx.shadowBlur = 0;
    }
    if(track.name){
      try{ ctx.letterSpacing = '0px'; }catch(e){}
      var nY = dyy + D + FH*0.045;
      fitFont(ctx, '700', Math.round(FW*0.05), fontD, track.name, FW*0.9);
      ctx.fillStyle = txtCol; ctx.fillText(track.name, cx, nY);
      try{ ctx.letterSpacing = '3px'; }catch(e){}
      ctx.font = '600 ' + Math.round(FW*0.021) + 'px ' + fontB;
      ctx.fillStyle = dimCol;
      ctx.fillText((track.meta || producer).toUpperCase(), cx, nY + FW*0.055);
      if(track.tag){  // persistent producer credit - rides along on reposts
        try{ ctx.letterSpacing = '2px'; }catch(e){}
        ctx.font = '600 ' + Math.round(FW*0.019) + 'px ' + fontB;
        ctx.fillStyle = accA(0.9);
        ctx.fillText(track.tag, cx, nY + FW*0.1);
      }
    }
    ctx.restore();
  }
  function drawCards(now){  // recorded intro/outro cards, matching the live .tcard/.ecard
    var a = 0, isEnd = false;
    if(endedAt && on('endcard')){ a = Math.min(1, (now - endedAt)/350); isEnd = true; }
    else if(!endedAt && on('titlecard')){ var e0 = (now - startT)/1000;
      if(e0 < 1.85) a = e0 < 0.35 ? e0/0.35 : (e0 < 1.5 ? 1 : Math.max(0, 1 - (e0 - 1.5)/0.35)); }
    if(a <= 0.001) return;
    ctx.save(); ctx.globalAlpha = a;
    ctx.fillStyle = 'rgba(5,5,8,.88)'; ctx.fillRect(0, 0, FW, FH);
    ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
    if(isEnd){
      fitFont(ctx, '700', Math.round(FW*0.062), fontD, producer, FW*0.86);
      ctx.fillStyle = txtCol; ctx.fillText(producer, FW/2, FH/2);
      if(track.tag){
        try{ ctx.letterSpacing = '3px'; }catch(e){}
        ctx.font = '500 ' + Math.round(FW*0.02) + 'px ' + fontB;
        ctx.fillStyle = accent; ctx.fillText(track.tag, FW/2, FH/2 + FW*0.06);
      }
    } else {
      fitFont(ctx, '700', Math.round(FW*0.062), fontD, track.name || '', FW*0.86);
      ctx.fillStyle = txtCol; ctx.fillText(track.name || '', FW/2, FH/2 - FW*0.012);
      try{ ctx.letterSpacing = '3px'; }catch(e){}
      ctx.font = '500 ' + Math.round(FW*0.022) + 'px ' + fontB;
      ctx.fillStyle = accent; ctx.fillText((track.meta || '').toUpperCase(), FW/2, FH/2 + FW*0.05);
    }
    ctx.restore();
  }

  // ---- the frame loop ----
  var bgTimer = 0;
  // hidden tabs pause requestAnimationFrame, which would freeze the recording;
  // fall back to a timer there (the export keeps playing audio, so Chrome
  // exempts the tab from aggressive timer throttling and rendering continues).
  function schedule(){
    if(typeof document !== 'undefined' && document.hidden){
      bgTimer = setTimeout(function(){ frame(performance.now()); }, 16);
    } else { raf = requestAnimationFrame(frame); }
  }
  function frame(now){
    if(!running) return;
    schedule();
    var dt = lastNow ? Math.min(0.05, (now - lastNow)/1000) : 0.016;
    lastNow = now;
    idleT += dt;
    playingNow = cfg.isPlaying ? !!cfg.isPlaying() : false;
    // analyser -> bass / energy / kick (turntable.js math)
    var an = cfg.getAnalyser ? cfg.getAnalyser() : null, bass = 0, energy = 0, i;
    if(an){ if(!dataArr || dataArr.length !== an.frequencyBinCount) dataArr = new Uint8Array(an.frequencyBinCount);
      an.getByteFrequencyData(dataArr);
      for(i = 0; i < 5; i++) bass += dataArr[i]; bass /= 1275;
      for(i = 0; i < dataArr.length; i++) energy += dataArr[i]; energy /= dataArr.length*255; }
    curEnergy = energy;
    if(eLong === 0 && energy > 0) eLong = energy;
    bassAvg = bassAvg*0.9 + bass*0.1; eLong = eLong*0.985 + energy*0.015;
    eMid += (energy - eMid)*0.06;
    var kick = Math.max(0, bass - bassAvg - 0.05);
    if(energy > eLong*1.45 + 0.12 && energy > 0.30 && now - lastDrop > 1400){ lastDrop = now; punch = 0.4; shake = Math.max(shake, 6); }
    var hov = cfg.hueOverride ? cfg.hueOverride() : null;
    if(hov != null && isFinite(hov)) hue = Number(hov); else hue = (hue + 0.04 + energy*0.45)%360;
    punch *= 0.9; shake *= 0.82;
    var fxParticles = on('particles'), fxSmoke = on('smoke'), fxShake = on('shake');
    if(kick > 0.05){ shake = Math.max(shake, Math.min(11, kick*34)); if(fxParticles && playingNow) spawnSparks(kick); }
    breath = 0.5 + 0.5*Math.sin(idleT*0.9);
    // progress -> heat (vinyl groove burn ramps later than the cassette hubs)
    var pr = cfg.getProgress ? cfg.getProgress() : 0;
    if(!isFinite(pr)) pr = 0; pr = Math.max(0, Math.min(1, pr));
    curProg = pr; heatVal = Math.min(1, pr*3);
    curHeat = Math.min(1, Math.max(0, (pr - 0.12)*3.4));
    // motion: spin only while the beat plays; the tonearm eases onto the groove
    if(playingNow){ rot += dt*TAU/3.4; reelRot += dt*TAU/1.7; }
    var armTarget = playingNow ? (-21 - pr*14) : 8;
    armA += (armTarget - armA)*Math.min(1, dt*5);
    var cyd = cyBase + Math.sin(idleT*TAU/7)*4*SHK;  // the deck's gentle float
    // spawners
    if(playingNow && fxParticles){ ambient(energy); bokehSpawn(energy); }
    if(playingNow && skin === 'vinyl' && fxParticles && on('dust')) dustSpawn();
    var chorus = Math.min(1, Math.max(0, (eMid - 0.25)/0.30));
    if(playingNow && fxParticles && chorus > 0.05){
      var rise = Math.floor(chorus*chorus*9);
      for(var ci = 0; ci < rise && embers.length < 200; ci++)
        embers.push({x: Math.random()*FW, y: FH + 8, vx: (Math.random()*2-1)*FW*0.0006,
          vy: -(FH*0.0017)*(0.7 + Math.random()*1.5)*(0.7 + chorus*0.8), life: 1,
          sz: ref*(0.004 + Math.random()*0.013)}); }
    var pts = smokePts(cyd);
    if(playingNow && fxSmoke && pts.length){
      var build = Math.max(0, energy - eLong*1.05);
      var prob = Math.min(0.7, build*6 + heatVal*0.14)*(skin === 'cassette' ? 1.35 : 1);
      for(var s = 0; s < pts.length; s++) if(Math.random() < prob) puff(pts[s].x, pts[s].y, heatVal);
    }
    // ---- draw ----
    ctx.save();
    var sx = fxShake ? (Math.random()*2-1)*shake*SHK : 0;
    var sy = fxShake ? (Math.random()*2-1)*shake*SHK : 0;
    var zs = 1 + Math.min(0.03, energy*0.04) + punch*0.06;
    ctx.translate(FW/2 + sx, FH/2 + sy); ctx.scale(zs, zs); ctx.translate(-FW/2, -FH/2);
    ctx.drawImage(bgSpr, -32, -32, FW + 64, FH + 64);
    drawHalo(cyd, energy);
    drawSpectrum(cyd, kick);
    if(skin === 'cassette') drawCassette(cyd, kick); else drawVinyl(cyd, kick);
    drawTexts(cyd);
    drawFX(cyd);
    ctx.restore();
    if(skin === 'cassette' && on('vhs')) drawVHS(now);
    if(on('vignette')) ctx.drawImage(vigSpr, 0, 0);
    drawCards(now);
  }

  function start(){ if(running) return; running = true;
    startT = performance.now(); lastNow = 0; schedule(); }
  function stop(){ running = false; if(raf) cancelAnimationFrame(raf); raf = 0;
    if(bgTimer) clearTimeout(bgTimer); bgTimer = 0; }
  function setEnded(){ if(!endedAt) endedAt = performance.now(); }

  return {canvas: canvas, start: start, stop: stop, setEnded: setEnded};
}
