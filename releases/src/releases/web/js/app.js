function applyTheme(name){document.documentElement.dataset.theme=name;try{localStorage.theme=name}catch(e){}
  const p=document.getElementById('themepick');if(p)p.value=name;}
applyTheme(new URLSearchParams(location.search).get("theme")||localStorage.theme||"editorial");

let TRACKS = [], GENRES = [], MONTHS = [], currentPlan = null;
let PRODUCER = 'Beats', COVER = null;  // for the in-app turntable label
window.__fxHue = null;  // null = auto (cover-art seeded, drifting)
let DECK = [], deckCur = -1, dactx, danalyser, ddata;
let reelSrc = null, reelStartAt = 0, reelDur = 0;  // reel plays the beat via Web Audio (no <audio> → no Chrome fullscreen media bar)

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) { const e = await r.json().catch(()=>({detail:r.statusText})); throw new Error(e.detail || r.statusText); }
  return r.json();
}
function toast(msg, bad) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.style.borderColor = bad ? 'var(--bad)' : 'var(--good)';
  t.style.display = 'block'; clearTimeout(t._t); t._t = setTimeout(()=>t.style.display='none', 3500);
}

async function loadTracks() {
  const data = await api('/api/tracks');
  TRACKS = data.tracks; GENRES = data.genres; MONTHS = data.months || [];
  PRODUCER = data.producer || 'Beats'; COVER = data.cover || null;
  document.getElementById('genres').innerHTML = GENRES.map(g=>`<option value="${g}">`).join('');
  document.getElementById('months').innerHTML = MONTHS.map(m=>`<option value="${m}">`).join('');
  const f = document.getElementById('filter'), cur = f.value;
  f.innerHTML = '<option value="">all</option>' + GENRES.map(g=>`<option>${g}</option>`).join('')
              + '<option value="unknown">unknown</option>';
  f.value = cur;
  const mf = document.getElementById('mfilter'), mcur = mf.value;
  mf.innerHTML = '<option value="">all</option>' + MONTHS.map(m=>`<option>${m}</option>`).join('');
  mf.value = mcur;
  updateRappers();
  render();
}

function updateRappers() {
  const set = new Set();
  TRACKS.forEach(t => {
    const s = (t.suitable_for || '').trim(); if (!s) return;
    set.add(s);  // the whole combo, e.g. "Drake, Travis Scott"
    s.split(',').map(x => x.trim()).filter(Boolean).forEach(n => set.add(n));  // and each name
  });
  document.getElementById('rappers').innerHTML =
    [...set].sort().map(n => `<option value="${esc(n)}">`).join('');
}

function render() {
  const fg = document.getElementById('filter').value;
  const fm = document.getElementById('mfilter').value;
  const shown = TRACKS.filter(t => (!fg || t.genre === fg) && (!fm || t.month === fm));
  document.getElementById('summary').textContent =
    `${shown.length} of ${TRACKS.length} tracks · ${GENRES.length} genres · ${MONTHS.length} months`;
  document.getElementById('dlBtn').textContent = `Download ${shown.length}`;
  document.getElementById('rows').innerHTML = shown.map(rowHtml).join('');
}

async function downloadZip() {
  const fg = document.getElementById('filter').value;
  const fm = document.getElementById('mfilter').value;
  const params = new URLSearchParams();
  if (fg) params.set('genre', fg);
  if (fm) params.set('month', fm);
  try {
    const r = await fetch('/api/download?' + params.toString());
    if (!r.ok) { const e = await r.json().catch(()=>({detail:r.statusText})); throw new Error(e.detail); }
    const blob = await r.blob();
    const cd = r.headers.get('Content-Disposition') || '';
    const m = cd.match(/filename="?([^"]+)"?/);
    const name = m ? m[1] : 'track-list.zip';
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = name; a.click();
    URL.revokeObjectURL(url);
    toast('Downloaded ' + name);
  } catch(e){ toast(e.message, true); }
}

async function buildPack() {
  const fg = document.getElementById('filter').value;
  const fm = document.getElementById('mfilter').value;
  const suggested = fg ? (fg[0].toUpperCase()+fg.slice(1)+' Pack') : (fm ? 'Pack '+fm : 'Beat Pack');
  const name = prompt('Pack name (clean-named audio + a player page + tracklist):', suggested);
  if (name === null) return;
  const prev = confirm('Protect the beats?\n\nOK = previews (trimmed to a hook + tag tone, can\'t be ripped)\nCancel = full beats');
  const params = new URLSearchParams();
  if (fg) params.set('genre', fg);
  if (fm) params.set('month', fm);
  if (name.trim()) params.set('name', name.trim());
  if (prev) params.set('preview', '1');
  try {
    if (prev) toast('Building protected previews (re-encoding)…');
    const r = await fetch('/api/pack?' + params.toString());
    if (!r.ok) { const e = await r.json().catch(()=>({detail:r.statusText})); throw new Error(e.detail); }
    const blob = await r.blob();
    const cd = r.headers.get('Content-Disposition') || '';
    const m = cd.match(/filename="?([^"]+)"?/);
    const fn = m ? m[1] : 'pack.zip';
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = fn; a.click();
    URL.revokeObjectURL(url);
    toast('Built ' + fn + (prev ? ' (previews)' : '') + ' — unzip → open index.html, or drop the folder on Netlify Drop for a link');
    const who = prompt('Log this send — who did you send it to? (blank to skip)');
    if (who && who.trim()) {
      const s = await api('/api/sent', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({contact: who.trim(), name: name.trim()||null, genre: fg||null, month: fm||null})});
      toast(`Logged: sent to ${s.contact} (${s.count} beats)`);
      loadTracks();  // refresh the "already sent" badges
    }
  } catch(e){ toast(e.message, true); }
}

function rowHtml(t) {
  const loc = t.filed ? `<span class="filed">✓ ${t.location}</span>`
                      : `<span class="loose">${t.location}</span>`;
  const g = t.genre === 'unknown' ? '' : t.genre;
  return `<tr data-id="${t.id}">
    <td><button class="play" onclick="play('${t.id}')" ${t.taggable?'':'title="not mp3"'}>▶</button></td>
    <td class="name">${esc(t.name)}${sentBadge(t)}</td>
    <td class="meta">${t.bpm?t.bpm+' bpm':''} ${t.key||''}</td>
    <td><input class="ginput" list="genres" value="${esc(g)}" placeholder="${t.genre}"
         onchange="setGenre('${t.id}', this.value)"></td>
    <td><span class="pill ${t.mix==='mixed'?'on':'off'}" onclick="toggle('${t.id}','mix','${t.mix}')">${t.mix}</span></td>
    <td><span class="pill ${t.master==='mastered'?'on':'off'}" onclick="toggle('${t.id}','master','${t.master}')">${t.master}</span></td>
    <td><input class="ginput" style="width:150px" value="${esc(t.artists)}" placeholder="—"
         onchange="setText('${t.id}','artists',this.value)"></td>
    <td><input class="ginput" list="months" style="width:88px" value="${esc(t.month)}" placeholder="—"
         onchange="setText('${t.id}','month',this.value)"></td>
    <td><input class="ginput" list="rappers" style="width:150px" value="${esc(t.suitable_for)}" placeholder="rappers…"
         onchange="setText('${t.id}','suitable_for',this.value)"></td>
    <td><input class="ginput" style="width:170px" value="${esc(t.notes)}" placeholder="—"
         onchange="setText('${t.id}','notes',this.value)"></td>
    <td>${loc}</td>
  </tr>`;
}
function esc(s){ return (s||'').replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function sentBadge(t){ return (t.sent_to && t.sent_to.length)
  ? ` <span class="senttag" title="Sent to: ${esc(t.sent_to.join(', '))}">✓ ${t.sent_to.length}</span>` : ''; }
async function showSends(){
  try{
    const {sends} = await api('/api/sends');
    document.getElementById('panelTitle').textContent = `Send history — ${sends.length} sends`;
    document.getElementById('planMoves').innerHTML = sends.length
      ? sends.map(s=>`<div class="move"><b>${esc(s.contact)}</b> — ${esc(s.pack)} · ${s.count} beats · ${new Date(s.sent_at*1000).toLocaleDateString()}</div>`).join('')
      : '<div class="move">No sends logged yet — build a pack and log who you sent it to.</div>';
    document.getElementById('panel').classList.add('show');
  }catch(e){ toast(e.message, true); }
}

function patch(updated) {
  const i = TRACKS.findIndex(t=>t.id===updated.id);
  if (i>=0) TRACKS[i] = updated;
  let listsChanged = false;
  if (updated.genre!=='unknown' && !GENRES.includes(updated.genre)) { GENRES.push(updated.genre); GENRES.sort(); listsChanged = true; }
  if (updated.month && !MONTHS.includes(updated.month)) { MONTHS.push(updated.month); MONTHS.sort().reverse(); listsChanged = true; }
  if (listsChanged) refreshFilters();
  updateRappers();
  render();
}
function refreshFilters() {
  const f = document.getElementById('filter'), cur = f.value;
  f.innerHTML = '<option value="">all</option>' + GENRES.map(g=>`<option>${g}</option>`).join('') + '<option value="unknown">unknown</option>';
  f.value = cur;
  const mf = document.getElementById('mfilter'), mcur = mf.value;
  mf.innerHTML = '<option value="">all</option>' + MONTHS.map(m=>`<option>${m}</option>`).join('');
  mf.value = mcur;
  document.getElementById('months').innerHTML = MONTHS.map(m=>`<option value="${m}">`).join('');
}

async function setGenre(id, value) {
  try { patch(await mark({id, genre: value.trim() || null})); }
  catch(e){ toast(e.message, true); }
}
async function setText(id, field, value) {
  try { patch(await mark({id, [field]: value})); } catch(e){ toast(e.message, true); }
}
async function toggle(id, field, current) {
  const next = field==='mix' ? (current==='mixed'?'unmixed':'mixed')
                             : (current==='mastered'?'unmastered':'mastered');
  try { patch(await mark({id, [field]: next})); } catch(e){ toast(e.message, true); }
}
function mark(body){ return api('/api/mark', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}); }

function play(id) {
  const t = TRACKS.find(x=>x.id===id);
  const a = document.getElementById('audio');
  a.src = '/api/audio?id=' + encodeURIComponent(id);
  document.getElementById('np').textContent = '♪ ' + (t?t.name:'');
  a.play().catch(()=>{});
}

// ---- in-app turntable: plays the FILTERED set live (numbering/copy is for download only) ----
const audioEl = document.getElementById('audio');
function cap(s){ return s ? s.charAt(0).toUpperCase()+s.slice(1) : s; }
function deckMeta(t){ return [t.bpm?t.bpm+' bpm':'', t.key||'', t.genre!=='unknown'?t.genre:''].filter(Boolean).join(' · '); }
function deckLabel(){
  [['label','label'],['clabel','clabel']].forEach(function(pair){
    const el=document.getElementById(pair[0]); if(!el) return;
    if (COVER) { el.className=pair[1]+' cover'; el.style.backgroundImage="url('"+COVER+"')"; el.textContent=''; }
    else { el.className=pair[1]; el.style.backgroundImage=''; el.textContent = PRODUCER; }
  });
}
function deckSkin(){ const ov=document.getElementById('ov'); const on=!ov.classList.contains('cassette-mode');
  deckSetSkin(ov, on?'cassette':'vinyl');
  document.getElementById('skintgl').textContent=on?'Vinyl':'Cassette'; deckSize(); }
// FX toggles — each chip flips an off-<name> class on #ov; all on by default
document.querySelectorAll('#ov .fxchip').forEach(function(ch){
  ch.onclick=function(){ ch.classList.toggle('off',
    document.getElementById('ov').classList.toggle('off-'+ch.dataset.fx)); };
});

function pickCover(){ document.getElementById('coverInput').click(); }
document.getElementById('coverInput').addEventListener('change', function(e){
  const f = e.target.files[0]; e.target.value=''; if(!f) return;
  if(f.size > 8*1024*1024){ toast('Image too large (max 8 MB)', true); return; }
  const reader = new FileReader();
  reader.onload = async function(){
    try{
      await api('/api/cover', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({name:f.name, data:reader.result})});
      COVER = '/api/cover?t=' + Date.now();   // cache-bust so the new art shows
      deckLabel();
      toast('Cover set — it\'s now the vinyl label + pack art');
    }catch(err){ toast(err.message, true); }
  };
  reader.readAsDataURL(f);
});
function openDeck(){
  const fg=document.getElementById('filter').value, fm=document.getElementById('mfilter').value;
  DECK = TRACKS.filter(t=>(!fg||t.genre===fg)&&(!fm||t.month===fm));
  if(!DECK.length){ toast('No tracks in this filter', true); return; }
  document.getElementById('ovtitle').textContent =
    (fg?cap(fg):'All beats') + (fm?' · '+fm:'') + ' · ' + DECK.length + ' beats';
  deckLabel();
  const ul=document.getElementById('ovlist'); ul.innerHTML='';
  DECK.forEach((t,i)=>{ const li=document.createElement('li');
    li.innerHTML='<span class="num"></span><span class="ti"></span><span class="me"></span>';
    li.querySelector('.num').textContent=String(i+1).padStart(2,'0');
    li.querySelector('.ti').textContent=t.name;
    li.querySelector('.me').textContent=deckMeta(t);
    li.onclick=()=>deckSelect(i); ul.appendChild(li); });
  document.getElementById('ov').classList.add('show');
  deckSize(); deckSelect(0);
}
function deckSelect(i){
  if(stemMode) _teardownStems();  // leaving remix when picking another beat
  deckCur=i; const t=DECK[i];
  audioEl.src='/api/audio?id='+encodeURIComponent(t.id);
  document.getElementById('ovnt').textContent=t.name;
  document.getElementById('ovnm').textContent=deckMeta(t);
  document.getElementById('np').textContent='♪ '+t.name;
  const nfo=document.querySelector('#ov .ovinfo'); nfo.classList.remove('nfin'); void nfo.offsetWidth; nfo.classList.add('nfin');
  const dot=document.getElementById('ovbpmdot');
  if(t.bpm){ dot.style.display='inline-block'; dot.style.animationDuration=(60/t.bpm).toFixed(3)+'s'; } else { dot.style.display='none'; }
  const hook=document.getElementById('ovhook');
  hook.textContent=[(t.genre&&t.genre!=='unknown')?t.genre.toUpperCase():'', t.bpm?t.bpm+' BPM':''].filter(Boolean).join(' · ');
  hook.classList.remove('pop'); void hook.offsetWidth; hook.classList.add('pop');
  [...document.getElementById('ovlist').children].forEach((li,j)=>li.classList.toggle('active',j===i));
  audioEl.play().catch(()=>{});
}
function deckTogglePlay(){
  if(stemMode){ toggleStemPlay(); return; }  // in remix, play/pause drives the looping stems, not the <audio>
  if(deckCur<0){ deckSelect(0); return; }
  audioEl.paused?audioEl.play():audioEl.pause();
}
function toggleStemPlay(){  // suspend/resume the whole stem graph — freezes audio, spin, progress + the crew together
  if(!dactx) return;
  if(dactx.state === 'running'){ dactx.suspend(); window.__stemPlaying = false; ovSetPlaying(false); }
  else { dactx.resume(); window.__stemPlaying = true; ovSetPlaying(true); }
}
function ovSetPlaying(p){
  deckSetPlaying(document.getElementById('ov'), p);
  document.getElementById('ovplay').innerHTML=p?'&#10074;&#10074;':'&#9654;';
}
function closeDeck(){ if(stemMode) _teardownStems(); document.getElementById('ov').classList.remove('show'); }
// Reel = full-frame record mode. Pressing it opens a setup panel (skin / effects /
// dancers); Start hides all top chrome, counts in 5s, then plays. Esc exits.
function deckReel(){ const ov=document.getElementById('ov');
  if(ov.classList.contains('reel')){ exitReel(); return; }
  openReelOpts(); }
function openReelOpts(){
  const ov=document.getElementById('ov');
  if(deckCur<0){ if(!DECK.length){ toast('Pick a beat first', true); return; } deckSelect(0); }
  const cassette=ov.classList.contains('cassette-mode');
  document.getElementById('ro-vinyl').classList.toggle('on', !cassette);
  document.getElementById('ro-cassette').classList.toggle('on', cassette);
  document.querySelectorAll('#reelopts [data-rofx]').forEach(ch =>
    ch.classList.toggle('off', ov.classList.contains('off-'+ch.dataset.rofx)));
  document.getElementById('ro-hueauto').classList.toggle('off', window.__fxHue!=null);
  document.getElementById('ro-crewrow').style.display = stemMode ? '' : 'none';  // dancers only exist in remix
  document.getElementById('ro-crew').classList.toggle('off', !ov.classList.contains('crewreel'));
  applyReelSize();
  document.getElementById('reelopts').classList.add('show');
}
function reelSetSkin(which){
  const ov=document.getElementById('ov');
  deckSetSkin(ov, which);
  document.getElementById('ro-vinyl').classList.toggle('on', which==='vinyl');
  document.getElementById('ro-cassette').classList.toggle('on', which==='cassette');
  deckSize();
}
let reelSize = (function(){ try{ return localStorage.getItem('reelSize') || 'm'; }catch(e){ return 'm'; } })();  // S/M/L deck size, remembered
function applyReelSize(){
  const ov=document.getElementById('ov');
  ov.classList.remove('size-xs','size-s','size-m','size-l'); ov.classList.add('size-'+reelSize);
  ['xs','s','m','l'].forEach(x=>{ const b=document.getElementById('ro-size-'+x); if(b) b.classList.toggle('on', x===reelSize); });
}
function reelSetSize(s){ reelSize=s; try{ localStorage.setItem('reelSize', s); }catch(e){} applyReelSize(); deckSize(); }
function reelToggleCrew(){
  const on=document.getElementById('ov').classList.toggle('crewreel');
  document.getElementById('ro-crew').classList.toggle('off', !on); layoutCrew();
}
function reelHueAuto(){ window.__fxHue=null; document.getElementById('ro-hueauto').classList.remove('off'); }
function reelHueSet(v){ window.__fxHue=Number(v); document.getElementById('ro-hueauto').classList.add('off'); }
// the panel's effect chips mirror the main fx chips (flip an off-<name> class on #ov)
document.querySelectorAll('#reelopts [data-rofx]').forEach(function(ch){
  ch.onclick=function(){ ch.classList.toggle('off',
    document.getElementById('ov').classList.toggle('off-'+ch.dataset.rofx)); };
});
function cancelReelOpts(){ document.getElementById('reelopts').classList.remove('show'); }
let _reelTimer=0;
function startReel(){
  const ov=document.getElementById('ov');
  document.getElementById('reelopts').classList.remove('show');
  ov.classList.add('reel'); document.getElementById('reeltgl').textContent='✕ Exit'; deckSize();
  // fullscreen the DECK overlay (not the whole page): hides Chrome's tabs AND keeps
  // the <audio> element outside the fullscreen, so Chrome shows no media-control overlay
  audioEl.removeAttribute('controls');
  try{ if(!document.fullscreenElement && ov.requestFullscreen) ov.requestFullscreen().catch(()=>{}); }catch(e){}
  // arm: stop + rewind so the beat begins fresh when the delay ends
  window.__reelBuf = null;
  if(stemMode){ if(dactx && dactx.state==='running') dactx.suspend(); window.__stemPlaying=false; }
  else { audioEl.pause(); try{ audioEl.currentTime=0; }catch(e){}
    // decode the beat into a buffer during the pre-roll, so reelGo can play it via Web Audio
    deckInitViz();
    if(dactx && deckCur>=0){ const id=DECK[deckCur].id;
      fetch('/api/audio?id='+encodeURIComponent(id)).then(r=>r.arrayBuffer()).then(ab=>dactx.decodeAudioData(ab))
        .then(buf=>{ window.__reelBuf=buf; }).catch(()=>{ window.__reelBuf=null; }); } }
  ovSetPlaying(false);
  // silent 5s pre-roll (no on-screen countdown) — time to hit record in OBS
  _reelTimer=setTimeout(()=>{ if(document.getElementById('ov').classList.contains('reel')) reelGo(); }, 5000);
}
function reelGo(){
  if(stemMode){ if(dactx) dactx.resume(); window.__stemPlaying=true; ovSetPlaying(true); return; }
  deckInitViz();
  if(window.__reelBuf && dactx){  // play via Web Audio — no media element, so no Chrome media overlay
    if(dactx.state==='suspended') dactx.resume();
    try{ if(reelSrc){ reelSrc.onended=null; reelSrc.stop(); reelSrc.disconnect(); } }catch(e){}
    reelSrc = dactx.createBufferSource(); reelSrc.buffer = window.__reelBuf; reelSrc.connect(danalyser);
    reelDur = window.__reelBuf.duration; reelStartAt = dactx.currentTime;
    reelSrc.onended = ()=>{ window.__reelPlaying=false; ovSetPlaying(false); showEndCard(); };  // natural end of the beat
    reelSrc.start(0); window.__reelPlaying=true; ovSetPlaying(true);
    showTitleCard();
  } else {  // decode not ready — fall back to the <audio> element (the media bar may appear)
    audioEl.play().then(()=>ovSetPlaying(true)).catch(()=>{});
  }
}
function exitReel(){
  clearTimeout(_reelTimer);
  const ov=document.getElementById('ov');
  ov.classList.remove('reel');
  document.getElementById('reeltgl').textContent='⤢ Reel';
  document.getElementById('reelopts').classList.remove('show');
  try{ if(reelSrc){ reelSrc.onended=null; reelSrc.stop(); reelSrc.disconnect(); } }catch(e){}
  reelSrc=null; window.__reelPlaying=false; window.__reelBuf=null;
  audioEl.setAttribute('controls', '');
  if(document.fullscreenElement){ try{ document.exitFullscreen(); }catch(e){} }
  deckSize();
}
// leaving browser fullscreen (Esc / green button) should also leave reel mode
document.addEventListener('fullscreenchange', ()=>{
  if(!document.fullscreenElement && document.getElementById('ov').classList.contains('reel')) exitReel();
});
function deckClean(){ const on=document.getElementById('ov').classList.toggle('clean');
  document.getElementById('cleantgl').textContent=on?'⛶ Exit clean':'⛶ Clean'; deckSize(); }
document.addEventListener('keydown', e=>{ if(e.key==='Escape'){ const ov=document.getElementById('ov');
  if(document.getElementById('reelopts').classList.contains('show')){ cancelReelOpts(); return; }
  if(ov.classList.contains('reel')){ exitReel(); return; }
  if(ov.classList.contains('clean') && !_recording){ ov.classList.remove('clean');
    document.getElementById('cleantgl').textContent='⛶ Clean'; deckSize(); } } });
const ocanvas=document.getElementById('viz');
function deckSize(){ const d=document.querySelector('#ov .deck'); if(!d)return; const s=d.clientWidth||320; ocanvas.width=s*2; ocanvas.height=s*2; layoutCrew(); }
function deckInitViz(){
  if(dactx){ if(dactx.state==='suspended')dactx.resume(); return; }
  try{ dactx=new (window.AudioContext||window.webkitAudioContext)();
    const src=dactx.createMediaElementSource(audioEl);
    danalyser=dactx.createAnalyser(); danalyser.fftSize=256; danalyser.smoothingTimeConstant=.6;
    src.connect(danalyser); danalyser.connect(dactx.destination);
  }catch(e){/* unsupported — vinyl still spins, audio still plays */}
}

// ---- stem remix: separate the beat into drums/bass/melody/vocals, loop them in
// sync through the visualizer's analyser, and toggle each layer live. ----
const STEM_LABEL = {drums:'Drums', bass:'Bass', other:'Melody', vocals:'Vocals'};
const STEM_ICON  = {drums:'🥁', bass:'🎸', other:'🎹', vocals:'🎤'};
const STEM_COLOR = {drums:'#f0663f', bass:'#9b6cf0', other:'#f0c64a', vocals:'#43c9b0'};
const STEM_DANCE = {drums:0, bass:1, other:2, vocals:3};  // each gets a distinct dance style
let stemMode = false, stemSources = [], stemGains = {}, stemOn = {}, stemAnalysers = {}, stemData = {}, _stemRaf = 0, stemNoteAt = {};
let stemStartAt = 0, stemLoopDur = 0, stemStartOffset = 0;  // for the visualizer: derive progress from the loop position, not the paused <audio>
let stemSolo = null;  // right-click solos a layer (mutes the rest); right-click it again restores everyone
let stemBpm = 0;      // the beat's BPM → the crew dances on the beat (falls back to ~120 if unknown)
let stemCrewEls = []; // cached {part,fig,arms,legs,stage} per character (avoids per-frame DOM queries)
function remixToggle(){ if(stemMode) exitStems(); else enterStems(); }
async function enterStems(){
  if(stemMode) return;
  if(deckCur < 0){ if(!DECK.length){ toast('Open the player and pick a beat first', true); return; } deckSelect(0); }
  const t = DECK[deckCur], btn = document.getElementById('remixbtn');
  btn.disabled = true; btn.textContent = '⏳ Separating…';
  toast('Separating stems (first time can take ~a minute)…');
  try{
    deckInitViz();
    if(!dactx || !danalyser){ throw new Error('audio not ready — press play once first'); }
    if(dactx.state === 'suspended') await dactx.resume();
    const r = await api('/api/stems', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id: t.id})});
    const buffers = {};
    await Promise.all(r.parts.map(async part => {
      const resp = await fetch(r.stems[part]); const ab = await resp.arrayBuffer();
      buffers[part] = await dactx.decodeAudioData(ab);
    }));
    audioEl.pause();
    stemSources = []; stemGains = {}; stemOn = {}; stemAnalysers = {}; stemData = {}; stemNoteAt = {};
    const startAt = dactx.currentTime + 0.15;  // all stems on one clock → locked in sync
    stemStartAt = startAt;
    stemLoopDur = Math.max(...r.parts.map(p => buffers[p].duration)) || 0;  // longest stem = the loop length
    stemBpm = (t && t.bpm) ? Number(t.bpm) : 0;  // dance on the beat
    // pick up roughly where the beat was playing so entering remix doesn't jump to 0:00
    stemStartOffset = (stemLoopDur && audioEl && isFinite(audioEl.currentTime)) ? (audioEl.currentTime % stemLoopDur) : 0;
    r.parts.forEach(part => {
      const src = dactx.createBufferSource(); src.buffer = buffers[part]; src.loop = true;
      const g = dactx.createGain(); g.gain.value = 1;
      const an = dactx.createAnalyser(); an.fftSize = 128; an.smoothingTimeConstant = 0.55;
      src.connect(g); g.connect(an); an.connect(danalyser);  // per-stem analyser → its character bounces to ITS sound
      src.start(startAt, Math.min(stemStartOffset, Math.max(0, src.buffer.duration - 0.05)));
      stemSources.push(src); stemGains[part] = g; stemOn[part] = true;
      stemAnalysers[part] = an; stemData[part] = new Uint8Array(an.frequencyBinCount);
    });
    document.getElementById('stemcrew').innerHTML = r.parts.map(p =>
      `<div class="stemchar" data-part="${p}" style="--cc:${STEM_COLOR[p]||'#c9a227'}" title="tap: drop layer · right-click: solo"`
      + ` onclick="toggleStem('${p}')" oncontextmenu="soloStem('${p}');return false;">`
      + `<div class="charstage">${charSVG(p)}</div><div class="charname">${STEM_LABEL[p]||p}</div></div>`).join('');
    stemCrewEls = r.parts.map(p => {  // cache nodes once so the rAF loop does no DOM lookups
      const ch = document.querySelector('.stemchar[data-part="'+p+'"]'), fig = ch.querySelector('.charfig');
      return {part:p, ch, fig, stage:ch.querySelector('.charstage'), arms:fig.querySelectorAll('.charm'), legs:fig.querySelectorAll('.leg'), glow:-1};
    });
    document.getElementById('stembar').classList.add('show');
    document.getElementById('ov').classList.add('remix');  // shows the crew ringed around the deck
    stemSolo = null;
    stemMode = true; window.__stemPlaying = true;
    ovSetPlaying(true);
    layoutCrew();      // place the characters around the turntable
    stemBounce();  // start the character animation loop
    btn.textContent = '🎛 Remixing'; btn.disabled = false;
    toast('Remix on — tap a character to drop its layer, right-click to solo it');
  }catch(e){
    btn.textContent = '🎛 Remix'; btn.disabled = false;
    toast('Stems failed: ' + e.message, true);
  }
}
// little original mascots (NOT Incredibox's art) — a round buddy per stem with a
// distinguishing prop, coloured by the stem; built once, animated by stemBounce().
function charSVG(part){
  const acc = {
    drums:  '<path d="M18 15 a14 13 0 0 1 28 0 l0 1 -28 0 z" fill="rgba(255,255,255,.92)"/>',  // beanie
    bass:   '<rect x="20" y="20" width="24" height="6.5" rx="3" fill="#0d0d10"/>',              // shades
    other:  '<path d="M16 24 a16 16 0 0 1 32 0" stroke="#0d0d10" stroke-width="3.5" fill="none"/><rect x="12" y="22" width="7" height="13" rx="3.5" fill="#0d0d10"/><rect x="45" y="22" width="7" height="13" rx="3.5" fill="#0d0d10"/>',  // headphones
    vocals: '<rect x="46.5" y="29" width="3.4" height="15" rx="1.7" fill="#0d0d10"/><circle cx="48.2" cy="28" r="4.6" fill="#0d0d10"/>',  // mic
  }[part] || '';
  const eyes = part === 'bass' ? '' :
    '<circle cx="26.5" cy="23" r="2.7" fill="#0d0d10"/><circle cx="37.5" cy="23" r="2.7" fill="#0d0d10"/>';
  return '<svg class="charfig" viewBox="0 0 64 96">'
    + '<rect class="leg" x="22" y="64" width="8.5" height="29" rx="4.2" fill="var(--cc)"/>'
    + '<rect class="leg" x="33.5" y="64" width="8.5" height="29" rx="4.2" fill="var(--cc)"/>'
    // arm + hand grouped so the hand swings on the end of the arm. NB: class is "charm"
    // (NOT "arm") — "arm" collides with the turntable tonearm's #ov .arm styles.
    + '<g class="charm"><rect x="7" y="40" width="8" height="20" rx="4" fill="var(--cc)"/><circle cx="11" cy="63" r="6.2" fill="var(--cc)"/></g>'
    + '<g class="charm"><rect x="49" y="40" width="8" height="20" rx="4" fill="var(--cc)"/><circle cx="53" cy="63" r="6.2" fill="var(--cc)"/></g>'
    + '<rect x="17" y="36" width="30" height="34" rx="13" fill="var(--cc)"/>'
    + '<circle cx="32" cy="24" r="15" fill="var(--cc)"/>'
    + eyes
    + '<path d="M28 30 Q32 33.5 36 30" stroke="#0d0d10" stroke-width="2" fill="none" stroke-linecap="round"/>'
    + acc + '</svg>';
}
function layoutCrew(){  // place each character on a ring around the deck — uses layout offsets
  if(!stemMode) return;            // (transform/scroll-independent, so the live shake on #ov can't throw it off)
  const deck = document.querySelector('#ov .deck'); if(!deck) return;
  const cx = deck.offsetLeft + deck.offsetWidth/2;
  const cy = deck.offsetTop  + deck.offsetHeight/2;
  const R = deck.offsetWidth*0.5 + 60;
  const ANG = [145, 35, 215, 325];  // lower-left, lower-right, upper-left, upper-right (deg)
  document.querySelectorAll('#stemcrew .stemchar').forEach((ch, i) => {
    const a = ANG[i % ANG.length] * Math.PI/180;
    ch.style.left = (cx + Math.cos(a)*R).toFixed(0) + 'px';
    ch.style.top  = (cy + Math.sin(a)*R).toFixed(0) + 'px';
  });
}
function stemBounce(){  // each mascot dances — bob, sway, arm-swing + music notes — to its own stem
  if(!stemMode) return;
  if(!window.__stemPlaying){ _stemRaf = requestAnimationFrame(stemBounce); return; }  // paused — hold poses, don't keep dancing silently
  const now = (window.performance && performance.now) ? performance.now() : Date.now();
  for(let k = 0; k < stemCrewEls.length; k++){
    const el = stemCrewEls[k], part = el.part, an = stemAnalysers[part], buf = stemData[part];
    if(!an || !el.fig){ continue; }
    an.getByteFrequencyData(buf);
    let e = 0; for(let i=0;i<buf.length;i++) e += buf[i]; e /= buf.length*255;
    const fig = el.fig, arms = el.arms, legs = el.legs;
    if(stemOn[part]){
      // dance ON THE BEAT: phase comes from the track BPM (fallback ~120). Energy (e)
      // still scales the size of each move, so loud sections hit harder.
      const beatMs = stemBpm > 0 ? 60000/stemBpm : 500, beat = now / beatMs, PI = Math.PI;
      const half = Math.sin(beat*PI), full = Math.sin(beat*2*PI);  // half = per-2-beats, full = per-beat
      let bob = -Math.min(15, e*72), sway = 0;
      let sx = 1 + Math.min(0.10, e*0.45), sy = 1 + Math.min(0.18, e*0.7);
      let armA = 0, armB = 0, legA = 0, legB = 0;
      const style = (STEM_DANCE[part] != null) ? STEM_DANCE[part] : (k % 4);
      if(style === 0){            // drums — FIST PUMP, one punch per beat
        const pump = Math.max(0, Math.sin((beat % 1) * PI));          // 0→1→0 each beat
        armA = -24 - pump*80; armB = 16 + half*8;                     // left fist drives up; right arm loose
        legA = full*(3 + e*7); legB = -legA; bob = -Math.min(12, e*28) - pump*7;
      } else if(style === 1){     // bass — SPONGEBOB leg cross/uncross (feet scissor over each other)
        const cross = half * (15 + e*22);
        legA = cross; legB = -cross;                                   // both feet swing inward → cross, then open out
        armA = -12 + half*7; armB = 12 - half*7; sway = half*4;
      } else if(style === 2){     // melody — JUMP, one hop per beat
        const hop = Math.abs(Math.sin(beat*PI));                       // hop per beat
        bob = -hop*(20 + e*38); sy = 1 + hop*0.05; sx = 1 - hop*0.04;
        const tuck = hop*(12 + e*8); legA = tuck; legB = -tuck;
        const raise = (0.4 + hop*0.6) * Math.min(58, 22 + e*48); armA = -raise; armB = raise;
      } else {                    // vocals — PEANUT BUTTER JELLY: both hands up, waving side to side together
        const wave = half * 30;
        armA = 162 + wave; armB = 162 + wave;                          // both arms up, sweeping the SAME way (in sync)
        sway = -half * 6; legA = half*8; legB = half*8;                // body counter-sways; feet side-step together
      }
      fig.style.transform = 'translateY('+bob.toFixed(1)+'px) rotate('+sway.toFixed(1)+'deg) scale('+sx.toFixed(3)+','+sy.toFixed(3)+')';
      const glow = Math.round(3 + e*9);  // cheaper blur, only re-set when it actually changes (avoids redundant filter recalcs)
      if(glow !== el.glow){ fig.style.filter = 'drop-shadow(0 0 '+glow+'px var(--cc))'; el.glow = glow; }
      if(arms[0]) arms[0].style.transform = 'rotate('+armA.toFixed(1)+'deg)';
      if(arms[1]) arms[1].style.transform = 'rotate('+armB.toFixed(1)+'deg)';
      if(legs[0]) legs[0].style.transform = 'rotate('+legA.toFixed(1)+'deg)';
      if(legs[1]) legs[1].style.transform = 'rotate('+legB.toFixed(1)+'deg)';
      if(e > 0.52 && now - (stemNoteAt[part]||0) > 260) spawnNote(el.stage, part, now);  // pop a note on hits
    } else if(el.glow !== -1 || fig.style.transform){
      fig.style.transform = ''; fig.style.filter = ''; el.glow = -1;
      if(arms[0]) arms[0].style.transform = ''; if(arms[1]) arms[1].style.transform = '';
      if(legs[0]) legs[0].style.transform = ''; if(legs[1]) legs[1].style.transform = '';
    }
  }
  _stemRaf = requestAnimationFrame(stemBounce);
}
function spawnNote(stage, part, now){
  stemNoteAt[part] = now;
  const note = document.createElement('span'); note.className = 'note';
  note.textContent = ['♪','♫','♩'][Math.floor(Math.random()*3)];
  note.style.left = (24 + Math.random()*20).toFixed(0) + 'px';
  note.style.setProperty('--nx', (Math.random()*30-15).toFixed(0)+'px');
  note.style.setProperty('--nr', (Math.random()*44-22).toFixed(0)+'deg');
  stage.appendChild(note);
  setTimeout(()=>note.remove(), 1000);
}
function applyStem(part){  // fade stemOn[part] in/out over ~0.25s (not instant) + grey the character
  if(!stemGains[part] || !dactx) return;
  const g = stemGains[part].gain, t = dactx.currentTime;
  g.cancelScheduledValues(t); g.setValueAtTime(g.value, t);
  g.linearRampToValueAtTime(stemOn[part] ? 1 : 0, t + 0.25);  // 0.25s crossfade
  const ch = document.querySelector('.stemchar[data-part="'+part+'"]');
  if(ch) ch.classList.toggle('off', !stemOn[part]);
}
function toggleStem(part){  // left-click: drop / bring back one layer (manual override exits solo)
  if(!stemGains[part] || !dactx) return;
  stemSolo = null;
  stemOn[part] = !stemOn[part];
  applyStem(part);
}
function soloStem(part){  // right-click: only this layer plays; right-click the soloed one again to restore all
  if(!stemGains[part] || !dactx) return;
  if(stemSolo === part){ stemSolo = null; for(const p in stemGains){ stemOn[p] = true; applyStem(p); } }
  else { stemSolo = part; for(const p in stemGains){ stemOn[p] = (p === part); applyStem(p); } }
}
function _teardownStems(){  // stop the stem graph + animation + reset UI, WITHOUT touching normal playback
  cancelAnimationFrame(_stemRaf); _stemRaf = 0;
  stemSources.forEach(s => { try{ s.stop(); }catch(e){} try{ s.disconnect(); }catch(e){} });
  Object.values(stemGains).forEach(g => { try{ g.disconnect(); }catch(e){} });
  Object.values(stemAnalysers).forEach(a => { try{ a.disconnect(); }catch(e){} });
  stemSources = []; stemGains = {}; stemAnalysers = {}; stemData = {}; stemMode = false; window.__stemPlaying = false;
  stemStartAt = 0; stemLoopDur = 0; stemStartOffset = 0; stemSolo = null; stemBpm = 0; stemCrewEls = [];
  if(dactx && dactx.state === 'suspended') dactx.resume();  // in case it was paused mid-remix
  document.getElementById('stembar').classList.remove('show');
  document.getElementById('ov').classList.remove('remix', 'crewreel');
  const rb = document.getElementById('remixbtn'); if(rb) rb.textContent = '🎛 Remix';
}
function exitStems(){ if(!stemMode) return; _teardownStems(); if(deckCur >= 0) deckSelect(deckCur); }

// ---- recorded intro/outro cards (reel-panel chips: titlecard / endcard, default on) ----
function cardOn(n){ return !document.getElementById('ov').classList.contains('off-'+n); }
function showTitleCard(){
  if(!cardOn('titlecard')) return;
  const t = DECK[deckCur]; if(!t) return;
  document.getElementById('tc-title').textContent = t.name || '';
  document.getElementById('tc-meta').textContent =
    [PRODUCER, t.bpm ? t.bpm + ' BPM' : '', (t.genre && t.genre !== 'unknown') ? t.genre : ''].filter(Boolean).join(' · ');
  const el = document.getElementById('tcard');
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 1500);
}
function showEndCard(done){
  if(!cardOn('endcard')){ if(done) done(); return; }
  document.getElementById('ec-name').textContent = PRODUCER;
  const el = document.getElementById('ecard');
  el.classList.add('show');
  setTimeout(() => { el.classList.remove('show'); if(done) done(); }, 1100);
}
// ---- one-click reel export: capture THIS tab (getDisplayMedia) in Clean mode so
// the recording is EXACTLY the live web render with no UI chrome, + tab audio, then
// transcode to mp4. A captured tab keeps rendering even if you look away. ----
let _recording = false;
async function exportReel(){
  if(_recording) return;
  if(deckCur < 0){ if(!DECK.length){ toast('Open the player and pick a beat first', true); return; } deckSelect(0); }
  if(!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia){ toast('Tab recording needs Chrome/Edge', true); return; }
  const ov = document.getElementById('ov'), btn = document.getElementById('exportbtn');
  let stream;
  try{
    stream = await navigator.mediaDevices.getDisplayMedia({
      video: {frameRate: {ideal: 60, max: 60}}, audio: {channelCount: 2}, preferCurrentTab: true,
    });
  }catch(e){ toast('Recording cancelled', true); return; }
  const vtrack = stream.getVideoTracks()[0];
  if(!stream.getAudioTracks().length) toast('No tab audio — next time pick "This tab" and tick "Share tab audio"', true);
  _recording = true; btn.disabled = true;
  const wasClean = ov.classList.contains('clean');
  ov.classList.add('clean'); deckSize();  // hide all chrome so the capture is just the deck + effects
  const mp4mime = ['video/mp4;codecs=h264,aac','video/mp4'].find(m => MediaRecorder.isTypeSupported(m));
  const webmmime = ['video/webm;codecs=vp9,opus','video/webm;codecs=vp8,opus','video/webm'].find(m => MediaRecorder.isTypeSupported(m));
  const mime = mp4mime || webmmime, direct = !!mp4mime;
  const rec = new MediaRecorder(stream, {mimeType: mime, videoBitsPerSecond: 24_000_000, audioBitsPerSecond: 192_000});
  const chunks = []; rec.ondataavailable = e => { if(e.data && e.data.size) chunks.push(e.data); };
  let stopped = false;
  function cleanup(){ if(!wasClean){ ov.classList.remove('clean'); document.getElementById('cleantgl').textContent='⛶ Clean'; deckSize(); }
    btn.disabled = false; _recording = false; stream.getTracks().forEach(t => t.stop()); }
  rec.onstop = async () => {
    if(stopped) return; stopped = true; cleanup();
    const blob = new Blob(chunks, {type: direct ? 'video/mp4' : 'video/webm'});
    const fname = ((DECK[deckCur] && DECK[deckCur].name) || 'reel').replace(/[\\/]/g,'_') + '.mp4';
    try{
      let mp4;
      if(direct){ mp4 = blob; }
      else { toast('Transcoding to mp4…');
        const r = await fetch('/api/tomp4', {method:'POST', headers:{'Content-Type':'video/webm'}, body: blob});
        if(!r.ok){ const e = await r.json().catch(()=>({detail:r.statusText})); throw new Error(e.detail); }
        mp4 = await r.blob(); }
      const url = URL.createObjectURL(mp4);
      const a = document.createElement('a'); a.href = url; a.download = fname; a.click();
      URL.revokeObjectURL(url);
      toast('Reel exported — check your downloads');
    }catch(e){ toast('mp4 failed: ' + e.message, true); }
  };
  vtrack.addEventListener('ended', () => { if(rec.state !== 'inactive') rec.stop(); });  // user hit Chrome's "Stop sharing"
  deckInitViz();
  audioEl.currentTime = 0;
  try{ await audioEl.play(); }catch(e){}
  rec.start();
  showTitleCard();
  toast('Recording — it stops at the end of the beat (or hit Chrome\'s "Stop sharing" bar)');
  const dur = (audioEl.duration && isFinite(audioEl.duration)) ? audioEl.duration : 60;
  const guard = setTimeout(() => { if(rec.state !== 'inactive') rec.stop(); }, Math.min(180000, dur*1000 + 2600));
  audioEl.addEventListener('ended', () => { clearTimeout(guard); showEndCard(() => { if(rec.state !== 'inactive') rec.stop(); }); }, {once:true});
}
// shared turntable visualizer (glowing spectrum + halo + progress ring + beat throb/shake)
if (window.ttRun) ttRun({ canvas:ocanvas, audio:audioEl, getAnalyser:()=>danalyser,
  fxCanvas:document.getElementById('ovpfx'), smokeAt:()=>deckSmokeAt(document.getElementById('ov')),
  scene:document.getElementById('ov'),
  getLabel:()=>document.getElementById(document.getElementById('ov').classList.contains('cassette-mode')?'clabel':'label'),
  flash:document.querySelector('#ov .flash'), getCover:()=>COVER,
  hueOverride:()=>window.__fxHue,
  getSkin:()=>document.getElementById('ov').classList.contains('cassette-mode')?'cassette':'vinyl',
  fxOn:(n)=>!document.getElementById('ov').classList.contains('off-'+n),
  isPlaying:()=>window.__stemPlaying || window.__reelPlaying || !!(audioEl && !audioEl.paused),
  getProgress:()=>{
    if(window.__reelPlaying && dactx && reelDur) return Math.min(1, (dactx.currentTime - reelStartAt) / reelDur);  // reel buffer playback
    if(!stemMode || !dactx || !stemLoopDur) return null;  // stem mode: tonearm/ring/heat follow the loop, not the paused <audio>
    const pos = Math.max(0, dactx.currentTime - stemStartAt) + stemStartOffset; return (pos % stemLoopDur) / stemLoopDur; },
  reelGet:()=>document.getElementById('ov').classList.contains('reel'), sparksOn:true });
// drag the record to scrub the track; a tap toggles play
if (window.ttScrub) ttScrub({ vinyl:document.getElementById('vinyl'), audio:audioEl, secPerRev:4, onTap:deckTogglePlay });
// In remix mode the <audio> is deliberately paused while the stems loop, so its
// pause/ended events must NOT brake the deck — stemMode keeps the disk spinning.
audioEl.addEventListener('play',()=>{ if(stemMode)return; deckInitViz(); ovSetPlaying(true); });
audioEl.addEventListener('pause',()=>{ if(!stemMode) ovSetPlaying(false); });
audioEl.addEventListener('ended',()=>{
  if(stemMode) return;
  if(document.getElementById('ov').classList.contains('show') && deckCur>=0 && deckCur<DECK.length-1) deckSelect(deckCur+1);
  else ovSetPlaying(false);
});
addEventListener('resize',deckSize);

async function preview() {
  try {
    const p = await api('/api/organize/preview');
    currentPlan = p.plan_id;
    document.getElementById('applyBtn').disabled = !(p.moves.length || p.tag_count);
    document.getElementById('panelTitle').textContent =
      `Plan — ${p.moves.length} moves · ${p.tag_count} tag edits` + (p.notes.length?` · ${p.notes.length} skipped`:'');
    document.getElementById('planMoves').innerHTML =
      (p.moves.length ? p.moves.slice(0,200).map(m=>`<div class="move"><b>${esc(base(m.src))}</b> → ${esc(dir(m.dest))}</div>`).join('')
                      : '<div class="move">No folder moves — marks already filed.</div>')
      + p.notes.map(n=>`<div class="move" style="color:var(--warn)">· ${esc(n)}</div>`).join('');
    document.getElementById('panel').classList.add('show');
  } catch(e){ toast(e.message, true); }
}
function base(p){ return p.split('/').pop(); }
function dir(p){ const a=p.split('/'); a.pop(); return a.slice(-4).join('/'); }

async function applyPlan() {
  if (!currentPlan) return;
  if (!confirm('Apply this plan? Moves files and writes ID3 tags. Reversible with Undo.')) return;
  try {
    const r = await api('/api/organize/apply', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({plan_id:currentPlan})});
    toast(`Applied — ${r.moved} moved, ${r.tagged} tagged (run ${r.run_id||'—'})`);
    document.getElementById('panel').classList.remove('show');
    document.getElementById('applyBtn').disabled = true; currentPlan = null;
    loadTracks();
  } catch(e){ toast(e.message, true); }
}

async function undoLast() {
  try {
    const {runs} = await api('/api/runs');
    const last = runs.find(r=>r.status==='applied');
    if (!last) { toast('No applied run to undo', true); return; }
    if (!confirm(`Undo run ${last.run_id} (${last.moves} moves)?`)) return;
    await api('/api/organize/undo', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({run_id:last.run_id})});
    toast('Undone — folders and tags restored');
    loadTracks();
  } catch(e){ toast(e.message, true); }
}

loadTracks().then(()=>{
  const q = new URLSearchParams(location.search);
  if (q.has('deck')) { openDeck(); if (q.get('skin')==='cassette') deckSkin(); if (q.has('reel')) deckReel(); if (q.has('clean')) deckClean(); }
});
