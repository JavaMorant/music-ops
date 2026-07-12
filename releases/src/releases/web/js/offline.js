/* Offline spectrum analysis for the fast (WebCodecs) reel export.

   ttOfflineSpectrum(audioBuffer, opts) reproduces what the live deck
   AnalyserNode would have reported at each 60fps video frame, without playing
   the track: Blackman window over the most recent fftSize samples, radix-2
   FFT, 1/N magnitude scaling, AnalyserNode time smoothing
   (s = tau*prev + (1-tau)*current) and the -100..-30 dB -> 0..255 byte map.

   Deliberately mirrors the app's live analyser (app.js deckInitViz:
   fftSize 256 -> 128 bins, smoothingTimeConstant 0.6, default dB range), NOT
   a generic 512/0.8 setup - the goal is that the offline export looks
   identical to the realtime captureStream recording, which reads that exact
   analyser. Frames must be requested sequentially (0,1,2,...) so the
   smoothing state carries across frames like a real AnalyserNode. The lead-in
   (audio starts leadSec into the video) and the tail after the beat ends are
   silence, so the bars rise and decay exactly like the realtime recording. */

function ttOfflineSpectrum(buf, opts){
  'use strict';
  opts = opts || {};
  var fps = opts.fps || 60;
  var fftSize = opts.fftSize || 256;                       // live deck analyser size
  var tau = opts.smoothing != null ? opts.smoothing : 0.6; // live smoothingTimeConstant
  var minDb = opts.minDb != null ? opts.minDb : -100;      // AnalyserNode defaults
  var maxDb = opts.maxDb != null ? opts.maxDb : -30;
  var leadSec = opts.leadSec || 0;
  var bins = fftSize/2, sr = buf.sampleRate, len = buf.length;
  var lead = Math.round(leadSec*sr);

  // mono downmix once (AnalyserNode mixes its input down to mono)
  var mono = new Float32Array(len);
  var nch = buf.numberOfChannels || 1;
  for(var c = 0; c < nch; c++){
    var d = buf.getChannelData(c);
    for(var i = 0; i < len; i++) mono[i] += d[i]/nch;
  }

  // Blackman window, Web Audio flavour: a0 - a1*cos(2*pi*n/N) + a2*cos(4*pi*n/N)
  var TAU2 = Math.PI*2;
  var win = new Float32Array(fftSize);
  for(i = 0; i < fftSize; i++)
    win[i] = 0.42 - 0.5*Math.cos(TAU2*i/fftSize) + 0.08*Math.cos(2*TAU2*i/fftSize);

  // radix-2 iterative FFT tables (fftSize is a power of two: 256 by default)
  var levels = Math.round(Math.log(fftSize)/Math.LN2);
  var cosT = new Float32Array(fftSize/2), sinT = new Float32Array(fftSize/2);
  for(i = 0; i < fftSize/2; i++){ cosT[i] = Math.cos(TAU2*i/fftSize); sinT[i] = Math.sin(TAU2*i/fftSize); }
  var rev = new Uint32Array(fftSize);
  for(i = 0; i < fftSize; i++){
    var r = 0;
    for(var b = 0; b < levels; b++) r = (r << 1) | ((i >>> b) & 1);
    rev[i] = r;
  }
  var re = new Float32Array(fftSize), im = new Float32Array(fftSize);
  function fft(){
    var i2, j2, k2, t;
    for(i2 = 0; i2 < fftSize; i2++){ j2 = rev[i2];
      if(j2 > i2){ t = re[i2]; re[i2] = re[j2]; re[j2] = t;
        t = im[i2]; im[i2] = im[j2]; im[j2] = t; } }
    for(var size = 2; size <= fftSize; size *= 2){
      var half = size/2, step = fftSize/size;
      for(i2 = 0; i2 < fftSize; i2 += size){
        for(j2 = i2, k2 = 0; j2 < i2 + half; j2++, k2 += step){
          var ar = re[j2 + half], ai = im[j2 + half];
          var tr = ar*cosT[k2] + ai*sinT[k2];   // twiddle e^(-i*2*pi*k/N)
          var ti = ai*cosT[k2] - ar*sinT[k2];
          re[j2 + half] = re[j2] - tr; im[j2 + half] = im[j2] - ti;
          re[j2] += tr; im[j2] += ti;
        }
      }
    }
  }

  var prev = new Float32Array(bins);   // smoothing state, carried frame to frame
  var out = new Uint8Array(bins);      // reused - consume before the next frame()
  var scale = 1/fftSize, range = 255/(maxDb - minDb);

  function frame(idx){
    // the most recent fftSize samples at video time idx/fps, on a timeline of
    // [leadSec silence][the beat][silence...] - out of range reads are 0
    var end = Math.round(idx*sr/fps);
    for(var n = 0; n < fftSize; n++){
      var j = end - fftSize + n - lead;
      re[n] = (j >= 0 && j < len) ? mono[j]*win[n] : 0;
      im[n] = 0;
    }
    fft();
    for(var k = 0; k < bins; k++){
      var m = Math.sqrt(re[k]*re[k] + im[k]*im[k])*scale;
      var s = tau*prev[k] + (1 - tau)*m;
      if(!isFinite(s) || s < 0) s = 0;
      prev[k] = s;
      var v = s > 0 ? Math.round((20*Math.log(s)/Math.LN10 - minDb)*range) : 0;
      out[k] = v < 0 ? 0 : (v > 255 ? 255 : v);
    }
    return out;
  }

  return {frame: frame, bins: bins, fps: fps};
}
