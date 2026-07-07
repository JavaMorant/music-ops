
function ttRun(opts){
  var ctx=opts.canvas.getContext('2d'), fxctx=null;  // fxctx = full-screen particle canvas, if provided
  var bassAvg=0, eLong=0, eMid=0, shake=0, punch=0, flash=0, hue=42, lastDrop=0, seeded=false, dataArr=null, curEnergy=0;
  var sparks=[], embers=[], shocks=[], smoke=[], bokeh=[], dust=[], smoothV=null, idleT=0, vinylPulse=0;  // idleT drives the always-on motion; bokeh = soft floating background lights (Trap-Nation vibe); vinylPulse = eased kick level that makes the ring breathe
  var fxw=0, fxh=0, rcx=0, rcy=0, ref=0, fxLeft=0, fxTop=0, sclx=1, scly=1, curHeat=0, curProg=0, stylusAng=-0.7, stylusX=0, stylusY=0, hasStylus=false;  // particle field + record centre/scale + scene scale + heat + live stylus contact point (fx px)
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
  // beat-drop: just a gentle motion pulse now. (The explosive spark-burst +
  // shockwave AND the coloured flash/bloom blasts were removed — to be replaced later.)
  function onDrop(energy){punch=0.4;shake=6;}
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
    var playing=opts.isPlaying?opts.isPlaying():!!(opts.audio&&!opts.audio.paused);  // stem remix loops while the <audio> is paused
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
    var hov=opts.hueOverride?opts.hueOverride():null;  // reel hue chip: pin the palette
    if(hov!=null&&isFinite(hov))hue=Number(hov); else hue=(hue+0.04+energy*0.45)%360;  // a touch slower so the colour drift reads as calm, not strobing
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
    if(playing&&fxParticles){ambient(energy);bokehSpawn(energy);}  // embers + a lush field of floating lights — only while music plays; idle stays clean
    // brake-disc heat: builds toward the end of the track; drives the cassette
    // reels' red glow (via the --heat CSS var) and the smoke colour.
    var dur=(opts.audio&&opts.audio.duration&&isFinite(opts.audio.duration))?opts.audio.duration:0;
    var ct=opts.audio?(opts.audio.currentTime||0):0;
    // stem remix loops while the <audio> is paused — drive progress off the loop
    // position (getProgress) so the tonearm/ring/heat stay in sync with what's heard.
    var pg=opts.getProgress?opts.getProgress():null, hasPg=(pg!=null&&isFinite(pg));
    var pr=hasPg?Math.max(0,Math.min(1,pg)):(dur?ct/dur:0), heat=Math.min(1,pr*3);  // cassette reels reach full red by ~a third in
    curProg=pr;
    curHeat=hasPg?Math.min(1,Math.max(0,(pr-0.12)*3.4)):(dur?Math.min(1,Math.max(0,((ct-20)/dur)*3)):0);  // vinyl groove stays cold early, then ramps
    var build=Math.max(0,energy-eLong*1.05);  // energy rising above its running average = a build-up
    if(opts.scene)opts.scene.style.setProperty('--heat',(fxHeat?heat:0).toFixed(3));  // heat toggle → reels' red glow
    if(opts.scene)opts.scene.style.setProperty('--prog',pr.toFixed(4));  // tonearm tracks inward with track progress
    var cassette=opts.getSkin&&opts.getSkin()==='cassette';
    if(playing&&!cassette&&fxParticles&&on('dust'))dustSpawn();  // vinyl only: dust catching the light
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
      for(var ci=0;ci<rise && embers.length<200;ci++)
        embers.push({x:Math.random()*fxw,y:fxh+8,vx:(Math.random()*2-1)*fxw*0.0006,vy:-(fxh*0.0017)*(0.7+Math.random()*1.5)*(0.7+chorus*0.8),life:1,sz:ref*(0.004+Math.random()*0.013)});}
    draw(energy,kick,playing,breath);
    drawFX();  // particles, on the full-screen field
  }
  function ambient(energy){var cap=10+Math.floor(energy*60), n=1+Math.floor(energy*4);
    for(var q=0;q<n;q++){ if(embers.length<cap && Math.random()<0.5){
      embers.push({x:Math.random()*fxw,y:fxh+8,vx:(Math.random()*2-1)*fxw*0.0004,vy:-(fxh*0.0011)*(0.5+Math.random()*1.4)*(0.6+energy*1.3),life:1,sz:ref*(0.004+Math.random()*0.01)});}}}
  // slow-drifting dust motes catching the light — vinyl only
  function dustSpawn(){ if(dust.length>=36||Math.random()>0.3)return;
    dust.push({x:Math.random()*fxw,y:Math.random()*fxh,vx:(Math.random()*2-1)*fxw*0.00008,
      vy:-(fxh*0.00012)*(0.4+Math.random()),t:0,dt:0.0016+Math.random()*0.002,
      sz:ref*(0.0016+Math.random()*0.0034),ph:Math.random()*6.2832,tw:2+Math.random()*3});}
  // soft, slow, glowing orbs drifting across the whole field — the Trap-Nation bokeh
  // look. They fade in and out (t:0->1), sway gently, and are tinted around the hue.
  function bokehSpawn(energy){var cap=46+Math.floor(energy*100), n=1+Math.floor(energy*3);  // lush field — cheap now that orbs are blitted sprites, not per-frame gradients
    for(var q=0;q<n;q++){ if(bokeh.length<cap && Math.random()<0.7){
      var z=Math.random();  // depth: 0 = far (small, sharp, slow), 1 = near (big, soft, fast) → parallax
      bokeh.push({x:Math.random()*fxw,y:fxh*(0.15+Math.random()*1.05),
        vx:(Math.random()*2-1)*fxw*0.00012*(0.35+z*1.7),vy:-(fxh*0.0004)*(0.4+z*1.9)*(0.5+Math.random()*0.8),
        t:0,dt:0.0024+Math.random()*0.0038,sz:ref*(0.006+z*z*0.052),
        ph:Math.random()*6.2832,amp:ref*(0.035+z*0.17),z:z,
        hoff:(Math.random()*70-35),peak:(0.1+Math.random()*0.16)*(1-z*0.45)+energy*0.08});}}}
  // one soft orb, pre-rendered to an offscreen canvas and re-tinted only when the
  // (slowly drifting) hue moves — then blitted per particle. Far cheaper than a
  // fresh radial gradient per orb per frame, which is what kept the frame budget tight.
  var bokSpr=null, bokSprHue=-999;
  function bokehSprite(){
    if(!bokSpr){bokSpr=document.createElement('canvas');bokSpr.width=bokSpr.height=128;}
    var rh=Math.round(hue/6)*6;
    if(rh!==bokSprHue){bokSprHue=rh;var sc=bokSpr.getContext('2d');sc.clearRect(0,0,128,128);
      var g=sc.createRadialGradient(64,64,0,64,64,64);
      g.addColorStop(0,'hsla('+rh+',90%,72%,1)');
      g.addColorStop(0.4,'hsla('+rh+',88%,62%,0.4)');
      g.addColorStop(1,'hsla('+rh+',88%,60%,0)');
      sc.fillStyle=g;sc.beginPath();sc.arc(64,64,64,0,6.2832);sc.fill();}
    return bokSpr;}
  // a thin thread of smoke like the wisp off a match: rises in a wavering line and
  // barely widens; hotter = redder
  function puff(x,y,heat){if(smoke.length>120)return;
    smoke.push({bx:x,x:x,y:y,vy:-(fxh*0.0012)*(0.8+Math.random()*0.5),r:ref*0.003,life:1,heat:heat,ph:Math.random()*6.2832,amp:ref*(0.01+Math.random()*0.016)});}
  function draw(energy,kick,playing,breath){
    var W=opts.canvas.width,cx=W/2,cy=W/2,R0=W*0.36;
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
    // idle shimmer. The vinyl ring is pinned (bass at the bottom, treble at the top).
    var bars=cassette?80:96, half=bars/2, envAmp=playing?0.05:0.14;
    if(!smoothV||smoothV.length!==bars){smoothV=new Float32Array(bars);}
    ctx.save();ctx.shadowBlur=W*0.006;ctx.lineCap='round';  // smaller blur = cheaper per frame (96 bars); the sprite bokeh still frees budget if we ever want more glow
    if(cassette){var baseY=W*0.885,ctw=W*0.82,clx=W*0.09;ctx.lineWidth=W*0.012;  // sits in the gap below the cassette (bottom ~76%)
      var prc=curProg;
      for(var i=0;i<bars;i++){var idx=i<half?i:bars-1-i;
        var raw=dataArr?dataArr[Math.floor(idx/half*dataArr.length*0.7)]/255:0;
        var env=envAmp*(0.5+0.5*Math.sin(idleT*1.7+idx*0.5));
        smoothV[i]+=(Math.max(raw,env)-smoothV[i])*0.35;var v=smoothV[i];
        var fxp=i/(bars-1),x=clx+fxp*ctw,len=W*0.012+v*v*W*0.16,played=fxp<=prc;  // mirrored seek-style waveform: played part lit, rest dimmed
        var col=played?'hsl('+((hue+idx/half*46)%360).toFixed(0)+','+(80+v*20).toFixed(0)+'%,'+(56+v*20).toFixed(0)+'%)':'hsla(40,12%,'+(42+v*16).toFixed(0)+'%,.55)';
        ctx.strokeStyle=col;ctx.shadowColor=played?col:'transparent';ctx.beginPath();ctx.moveTo(x,baseY-len/2);ctx.lineTo(x,baseY+len/2);ctx.stroke();}}
    else{ctx.lineWidth=W*0.013;
      // Trap-Nation ring: bass anchored at the BOTTOM centre (canvas +y is down, so
      // +PI/2 = 6 o'clock), frequency climbing up BOTH sides (mirrored across the
      // vertical axis) to treble at the TOP. Snappy attack with a slower meter-style
      // fall, a perceptual (pow) bin map so the bass owns the bottom arc, kick-driven
      // bass bars, level-scaled glow, white-hot peak tips, and the whole ring
      // breathing outward on the beat.
      vinylPulse=Math.max(vinylPulse*0.92,Math.min(1,kick*4));
      var R0v=R0*(1+vinylPulse*0.022),maxV=W*0.483;
      ctx.shadowBlur=W*(0.005+Math.min(0.012,(energy*0.8+vinylPulse*0.5)*0.014));
      for(var b=0;b<bars;b++){var j2=b<half?b:bars-1-b,fp=j2/(half-1);
        var raw2=dataArr?Math.min(1,dataArr[Math.floor(Math.pow(fp,1.7)*dataArr.length*0.5)]/255*(1+fp*0.55)):0;
        var env2=envAmp*(0.5+0.5*Math.sin(idleT*1.7+j2*0.5)),tg2=Math.max(raw2,env2);
        smoothV[b]+=(tg2-smoothV[b])*(tg2>smoothV[b]?0.65:0.18);var w2=smoothV[b];
        var l2=Math.min(W*0.010+Math.pow(w2,1.5)*W*0.155+kick*(1-fp)*W*0.07,maxV-R0v);
        var a2=b/bars*6.2832+1.5708,c2=Math.cos(a2),s2=Math.sin(a2);
        var hb=((hue+fp*40)%360).toFixed(0),k2='hsl('+hb+','+(70+w2*30).toFixed(0)+'%,'+(50+w2*26).toFixed(0)+'%)';
        ctx.strokeStyle=k2;ctx.shadowColor=k2;ctx.beginPath();ctx.moveTo(cx+c2*R0v,cy+s2*R0v);ctx.lineTo(cx+c2*(R0v+l2),cy+s2*(R0v+l2));ctx.stroke();
        if(w2>0.7){ctx.save();ctx.globalAlpha=Math.min(1,(w2-0.7)*3)*0.85;var kw='hsl('+hb+',100%,88%)';
          ctx.strokeStyle=kw;ctx.shadowColor=kw;ctx.lineWidth=W*0.008;
          ctx.beginPath();ctx.moveTo(cx+c2*(R0v+l2*0.55),cy+s2*(R0v+l2*0.55));ctx.lineTo(cx+c2*(R0v+l2),cy+s2*(R0v+l2));ctx.stroke();ctx.restore();}}}
    ctx.restore();
    if(!cassette && (curProg>0 || playing)){var pr=curProg;
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
    if(dust.length){c.save();
      for(var di=dust.length-1;di>=0;di--){var d=dust[di];d.t+=d.dt;
        if(d.t>=1){dust.splice(di,1);continue;}
        d.x+=d.vx+Math.sin(d.t*6.2832*d.tw+d.ph)*fxw*0.00018;d.y+=d.vy;
        var da=Math.sin(d.t*3.14159)*(0.1+0.16*Math.sin(d.t*6.2832*d.tw*1.7+d.ph));
        if(da<=0)continue;
        c.globalAlpha=Math.min(0.3,da);c.fillStyle='rgba(255,248,235,1)';
        c.beginPath();c.arc(d.x,d.y,d.sz,0,6.2832);c.fill();}
      c.restore();c.globalAlpha=1;}
    if(bokeh.length){c.save();c.globalCompositeOperation='lighter';  // additive — per-particle radial gradient (crisper + per-orb hue; a touch more per-frame cost than the sprite)
      for(var bi=bokeh.length-1;bi>=0;bi--){var bo=bokeh[bi];bo.t+=bo.dt;
        if(bo.t>=1){bokeh.splice(bi,1);continue;}
        bo.y+=bo.vy;bo.x+=bo.vx+Math.sin(bo.t*6.2832+bo.ph)*bo.amp*0.012;
        var ba=Math.sin(bo.t*3.14159)*bo.peak,bh=(hue+bo.hoff+360)%360,mid=(0.42-(bo.z||0)*0.3);  // near orbs (high z) = softer core
        var bg=c.createRadialGradient(bo.x,bo.y,0,bo.x,bo.y,bo.sz);
        bg.addColorStop(0,'hsla('+bh.toFixed(0)+',90%,72%,'+ba.toFixed(3)+')');
        bg.addColorStop(mid.toFixed(2),'hsla('+bh.toFixed(0)+',88%,62%,'+(ba*0.4).toFixed(3)+')');
        bg.addColorStop(1,'hsla('+bh.toFixed(0)+',88%,60%,0)');
        c.fillStyle=bg;c.beginPath();c.arc(bo.x,bo.y,bo.sz,0,6.2832);c.fill();}
      c.restore();}
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
