"""Pack export tests — clean naming, the player page + tracklist, dedupe, and
that the library (sources) is never modified."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from releases import pack as packmod
from releases import preview as previewmod

DECK_DIR = Path(packmod.__file__).parent / "web" / "deck"


def _real_audio(path: Path, seconds: int = 20) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency=200:duration={seconds}", "-y", str(path)],
        check=True,
    )
    return path


def _dur(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    return float(out)


def _track(tmp_path, fname, title, bpm=None, key=None, genre="unknown", artists="",
           suitable_for="", notes=""):
    src = tmp_path / "lib" / fname
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"\xff\xfb\x90\x00" + b"audio")
    return packmod.PackTrack(src=src, title=title, bpm=bpm, key=key, genre=genre,
                             artists=artists, suitable_for=suitable_for, notes=notes)


def _meta(name="Trap Pack"):
    return packmod.PackMeta(name=name, producer="Dibs", made_on="2026-06-20", contact="dibs@x.com")


def test_clean_track_filename():
    t = packmod.PackTrack(src=Path("/x/a.mp3"), title="My Beat", bpm=140, key="Fm", genre="trap", artists="")
    assert packmod.clean_track_filename(1, t) == "01 - My Beat [140 Fm].mp3"
    t2 = packmod.PackTrack(src=Path("/x/b.mp3"), title="No Meta", bpm=None, key=None, genre="unknown", artists="")
    assert packmod.clean_track_filename(2, t2) == "02 - No Meta.mp3"


def test_safe_filename_strips_separators():
    assert "/" not in packmod.safe_filename("a/b\\c") and "\\" not in packmod.safe_filename("a/b\\c")


def test_build_pack_writes_audio_player_and_tracklist(tmp_path):
    tracks = [
        _track(tmp_path, "x.mp3", "Encara", bpm=129, key="F", genre="jersey club"),
        _track(tmp_path, "y.mp3", "Gqom Thing", bpm=150, key="Gm", genre="gqom", artists="Jah"),
    ]
    out = tmp_path / "out" / "Trap Pack"
    names = packmod.build_pack(tracks, out, _meta())
    assert names == ["01 - Encara [129 F].mp3", "02 - Gqom Thing [150 Gm].mp3"]
    for n in names:
        assert (out / n).exists()
    idx = (out / "index.html").read_text()
    assert "Encara" in idx and "Produced by" in idx and "Dibs" in idx
    assert "01 - Encara [129 F].mp3" in idx  # the audio is wired into the player
    assert '‘' not in idx and '’' not in idx  # editor must not convert ASCII quotes to curly ones
    tl = (out / "tracklist.txt").read_text()
    assert "Encara" in tl and "150 BPM" in tl and "feat. Jah" in tl


def test_suitable_for_and_notes_in_tracklist_and_player(tmp_path):
    t = _track(tmp_path, "x.mp3", "Encara", bpm=140, key="Fm", genre="trap",
               suitable_for="Drake, Travis Scott", notes="open for placement")
    out = tmp_path / "out" / "p"
    packmod.build_pack([t], out, _meta())
    tl = (out / "tracklist.txt").read_text()
    assert "suitable for: Drake, Travis Scott" in tl
    assert "note: open for placement" in tl
    idx = (out / "index.html").read_text()
    assert "Drake, Travis Scott" in idx        # suitable_for wired into the player
    assert '"sf":' in idx and "for '+t.sf" in idx
    assert "open for placement" in idx         # notes wired into the player too
    assert '"n":' in idx and "'.nt2'" in idx


def test_zip_pack_is_one_top_folder_ready_to_send(tmp_path):
    import zipfile
    out = tmp_path / "out" / "Trap Pack"
    packmod.build_pack([_track(tmp_path, "x.mp3", "Beat", bpm=140)], out, _meta())
    z = tmp_path / "Trap Pack.zip"
    assert packmod.zip_pack(out, z) == z and z.exists()
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
    assert names and all(n.startswith("Trap Pack/") for n in names)  # single named top folder
    assert any(n.endswith("/index.html") for n in names)
    assert any(n.endswith("/tracklist.txt") for n in names)


def test_clean_rebuild_drops_stale_beats_from_zip(tmp_path):
    """Re-running a pack into the same folder with fewer tracks must not ship the
    old beats in the zip (the auto-zip regression the reviewer caught)."""
    import zipfile
    out = tmp_path / "out" / "Pack"
    packmod.build_pack([_track(tmp_path, "a.mp3", "Alpha", bpm=140),
                        _track(tmp_path, "b.mp3", "Beta", bpm=141)], out, _meta())
    assert (out / "01 - Alpha [140].mp3").exists()
    # rebuild with a single, different track + clean
    packmod.build_pack([_track(tmp_path, "c.mp3", "Gamma", bpm=142)], out, _meta(), clean=True)
    mp3s = sorted(p.name for p in out.glob("*.mp3"))
    assert mp3s == ["01 - Gamma [142].mp3"]    # Alpha/Beta gone, not just unlinked
    z = tmp_path / "Pack.zip"
    packmod.zip_pack(out, z)
    with zipfile.ZipFile(z) as zf:
        assert sum(n.endswith(".mp3") for n in zf.namelist()) == 1


@pytest.mark.skipif(not previewmod.has_ffmpeg(), reason="ffmpeg not installed")
def test_make_preview_trims_tags_and_keeps_source(tmp_path):
    src = _real_audio(tmp_path / "beat.wav", 20)
    before = src.stat().st_size
    dest = tmp_path / "prev.mp3"
    previewmod.make_preview(src, dest, seconds=8)
    assert dest.exists() and 7.0 <= _dur(dest) <= 9.0      # trimmed to ~8s
    assert src.exists() and src.stat().st_size == before    # source untouched (read-only)


@pytest.mark.skipif(not previewmod.has_ffmpeg(), reason="ffmpeg not installed")
def test_build_pack_preview_is_mp3_and_short(tmp_path):
    src = _real_audio(tmp_path / "lib" / "x.wav", 20)
    t = packmod.PackTrack(src=src, title="Beat", bpm=140, key="Fm", genre="trap", artists="")
    out = tmp_path / "out" / "p"
    names = packmod.build_pack([t], out, _meta(), preview=True, preview_seconds=6)
    assert names == ["01 - Beat [140 Fm].mp3"]              # re-encoded to mp3, not the .wav
    assert (out / names[0]).exists() and _dur(out / names[0]) <= 7.5
    assert "01 - Beat [140 Fm].mp3" in (out / "index.html").read_text()  # wired into the player
    assert src.read_bytes()                                 # source still there, untouched


def test_inquire_button_when_contact_has_email(tmp_path):
    out = tmp_path / "out" / "p"
    packmod.build_pack([_track(tmp_path, "x.mp3", "Encara", bpm=140, key="Fm")], out,
                       packmod.PackMeta(name="Trap Pack", producer="Dibs",
                                        made_on="2026-06-23", contact="hit me: dibs@beats.com"))
    idx = (out / "index.html").read_text()
    assert "function inquire" in idx and "'mailto:'+INQ.contact" in idx   # per-beat mailto
    assert '"contact": "dibs@beats.com"' in idx                          # email pulled from the contact line
    assert "className='inq'" in idx and "e.stopPropagation()" in idx     # button doesn't trigger play


def test_inquire_hidden_without_email(tmp_path):
    out = tmp_path / "out" / "p"
    packmod.build_pack([_track(tmp_path, "x.mp3", "Encara", bpm=140)], out,
                       packmod.PackMeta(name="Trap Pack", producer="Dibs",
                                        made_on="2026-06-23", contact="@dibshandle"))
    idx = (out / "index.html").read_text()
    assert '"contact": ""' in idx   # no email → INQ.contact falsy → no Inquire buttons rendered


def test_build_pack_does_not_touch_sources(tmp_path):
    t = _track(tmp_path, "x.mp3", "Encara", bpm=129, key="F")
    before = t.src.read_bytes()
    packmod.build_pack([t], tmp_path / "out" / "p", _meta())
    assert t.src.exists() and t.src.read_bytes() == before  # source untouched (copy, not move)


def test_build_pack_dedupes_collisions(tmp_path):
    tracks = [_track(tmp_path, "a.mp3", "Same", bpm=140, key="Fm"),
              _track(tmp_path, "b.mp3", "Same", bpm=140, key="Fm")]
    names = packmod.build_pack(tracks, tmp_path / "out" / "p", _meta())
    assert len(set(names)) == 2  # no overwrite — second is uniquely named


def test_index_html_escapes_titles(tmp_path):
    t = _track(tmp_path, "x.mp3", "<script>alert(1)</script>", bpm=120, key="C")
    packmod.build_pack([t], tmp_path / "out" / "p", _meta())
    idx = (tmp_path / "out" / "p" / "index.html").read_text()
    # the title is injected as JSON in a <script>; < > & are escaped so it can't break out
    assert "<script>alert(1)</script>" not in idx
    assert "\\u003cscript\\u003e" in idx


def test_index_html_is_a_turntable_player(tmp_path):
    t = _track(tmp_path, "x.mp3", "Beat One", bpm=140, key="Fm", genre="trap")
    packmod.build_pack([t], tmp_path / "out" / "p", _meta())
    idx = (tmp_path / "out" / "p" / "index.html").read_text()
    assert 'class="vinyl"' in idx and "@keyframes spin" in idx  # the spinning record
    assert 'id="viz"' in idx and "createAnalyser" in idx        # the audio-reactive visualizer
    assert '"f": "01 - Beat One [140 Fm].mp3"' in idx           # the track wired into the player


def test_reel_mode_present(tmp_path):
    packmod.build_pack([_track(tmp_path, "x.mp3", "B", bpm=140)], tmp_path / "out" / "p", _meta())
    idx = (tmp_path / "out" / "p" / "index.html").read_text()
    assert 'id="reelbtn"' in idx                       # the Reel toggle button
    assert "body.reel" in idx                           # the 9:16 reel layout rules
    assert "classList.toggle('reel')" in idx


def test_reactive_effects_present(tmp_path):
    packmod.build_pack([_track(tmp_path, "x.mp3", "B", bpm=140)], tmp_path / "out" / "p", _meta())
    idx = (tmp_path / "out" / "p" / "index.html").read_text()
    assert "function ttRun" in idx                      # shared visualizer inlined
    assert 'class="gloss"' in idx and 'class="fx"' in idx  # sheen + vignette/grain
    assert "createRadialGradient" in idx                # the bass halo
    assert "opts.scene.style.transform" in idx          # the beat-synced screen shake
    assert "function ttScrub" in idx and "secPerRev" in idx  # drag-the-record scrubbing
    assert ".arm.on{transform:rotate(calc(-21deg" in idx  # playing = needle DOWN on the record, sweeping inward


def test_dopamine_effects_present(tmp_path):
    packmod.build_pack([_track(tmp_path, "x.mp3", "B", bpm=140, genre="trap")],
                       tmp_path / "out" / "p", _meta())
    idx = (tmp_path / "out" / "p" / "index.html").read_text()
    assert "function onDrop" in idx and "lastDrop" in idx        # drop detection + payoff
    assert "hsl('+(" in idx or "hsla('+hue" in idx               # living colour from a drifting hue
    assert "embers" in idx                                       # ambient particle field
    assert 'class="flash"' in idx and "shocks" in idx            # flash + expanding shockwave (no emoji)
    assert "\U0001f525" not in idx                               # the fire emoji is gone
    assert 'id="hook"' in idx and "hookpop" in idx              # bold hook text
    assert '"g": "trap"' in idx                                  # genre wired for the hook


def test_idle_animation_present(tmp_path):
    packmod.build_pack([_track(tmp_path, "x.mp3", "B", bpm=140)], tmp_path / "out" / "p", _meta())
    idx = (tmp_path / "out" / "p" / "index.html").read_text()
    assert "@keyframes float" in idx                     # the deck gently floats even at rest
    assert "idleT" in idx                                 # always-on motion clock
    assert "smoothV" in idx and "*0.35" in idx           # spectrum bars ease (flow, not jitter)
    assert "envAmp=playing?0.05:0.14" in idx             # idle shimmer keeps the bars alive when paused
    assert "ringRot" in idx                               # the spectrum slowly revolves


def test_fullscreen_particles_and_shake(tmp_path):
    packmod.build_pack([_track(tmp_path, "x.mp3", "B", bpm=140)], tmp_path / "out" / "p", _meta())
    idx = (tmp_path / "out" / "p" / "index.html").read_text()
    assert 'class="pfx"' in idx                                  # the full-screen particle canvas exists
    assert "fxCanvas:document.getElementById('pfx')" in idx      # wired into the visualizer
    assert "fx.width/fr.width" in idx                            # coords account for a scaled scene (reel mode)
    assert "function drawFX" in idx and "opts.fxCanvas" in idx   # particles draw on the full-screen field
    assert "rcx=(dr.left" in idx                                 # burst emanates from the record's on-screen centre
    assert "sx=fxShake?(Math.random()" in idx                    # screen shakes on kicks (gated by the Shake toggle)


def test_brake_heat_and_smoke_present(tmp_path):
    packmod.build_pack([_track(tmp_path, "x.mp3", "B", bpm=140)], tmp_path / "out" / "p", _meta())
    idx = (tmp_path / "out" / "p" / "index.html").read_text()
    # reel hubs heat up like brake discs; full redness by ~a third of the way in
    assert "--heat" in idx and "heat=Math.min(1,pr*3)" in idx
    assert "((ct-20)/dur)*3" in idx                              # the vinyl groove stays cold for the first ~20s, then ramps
    assert "color-mix(in srgb" in idx                            # the hub tints red with heat
    assert "setProperty('--heat'" in idx
    # smoke rises from the hot reels / the stylus, denser + redder over time
    assert "function puff" in idx and "smoke.push" in idx
    assert "function deckSmokeAt" in idx and "armtip" in idx      # stylus anchor on the tonearm
    assert "#cassette .reel" in idx                              # smoke from both reels in cassette mode


def test_buildup_smoke_and_burnt_trail(tmp_path):
    packmod.build_pack([_track(tmp_path, "x.mp3", "B", bpm=140)], tmp_path / "out" / "p", _meta())
    idx = (tmp_path / "out" / "p" / "index.html").read_text()
    assert "energy-eLong*1.05" in idx and "build*6" in idx       # smoke starts when the build-up starts
    assert "if(eLong===0&&energy>0)eLong=energy" in idx          # ...and the intro isn't mistaken for one long build-up
    assert "r:ref*0.003" in idx and "Math.sin(age*9" in idx      # thin, wavering match-like smoke threads
    assert "hasStylus?stylusX" in idx                            # the red contact circle sits at the stylus tip on the vinyl
    # the groove spirals inward and the tonearm tracks it (like a real record)
    assert "scorched band the needle has already crossed" in idx
    assert "setProperty('--prog'" in idx and "var(--prog,0)*14deg" in idx


def test_chorus_particle_burst(tmp_path):
    packmod.build_pack([_track(tmp_path, "x.mp3", "B", bpm=140)], tmp_path / "out" / "p", _meta())
    idx = (tmp_path / "out" / "p" / "index.html").read_text()
    assert "eMid+=(energy-eMid)" in idx                          # smoothed section energy
    assert "var chorus=Math.min(1" in idx                        # loud/full sections = best guess at the chorus
    assert "streaming up from the bottom" in idx                 # the excess rises from the bottom, not off the deck
    assert "chorus*chorus*9" in idx and "embers.length<200" in idx


def test_fx_toggles_present(tmp_path):
    packmod.build_pack([_track(tmp_path, "x.mp3", "B", bpm=140)], tmp_path / "out" / "p", _meta())
    idx = (tmp_path / "out" / "p" / "index.html").read_text()
    # generic on/off mechanism + a chip per effect, all on by default
    assert "fxOn:function(n){return !document.body.classList.contains('off-'+n)" in idx
    for fx in ("smoke", "particles", "shake", "heat"):
        assert f'data-fx="{fx}"' in idx
    # each effect is actually gated in the visualizer
    assert all(g in idx for g in ("fxSmoke", "fxParticles", "fxShake", "fxHeat"))
    # fire is fully removed
    assert "drawFire" not in idx and "fxFire" not in idx and 'data-fx="fire"' not in idx


def test_cassette_skin_present(tmp_path):
    packmod.build_pack([_track(tmp_path, "x.mp3", "B", bpm=140)], tmp_path / "out" / "p", _meta())
    idx = (tmp_path / "out" / "p" / "index.html").read_text()
    assert 'class="cassette"' in idx and 'class="reel"' in idx   # the cassette + spinning reels
    assert "cassette-mode" in idx and 'id="skinbtn"' in idx      # toggle to switch skins
    assert "getSkin" in idx                                       # visualizer is skin-aware
    assert "baseY-len/2" in idx and "played=fxp<=prc" in idx      # mirrored seek-style cassette waveform


def test_cover_seeds_visualizer_colour(tmp_path):
    cover = tmp_path / "art.png"
    cover.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)
    packmod.build_pack([_track(tmp_path, "x.mp3", "B", bpm=140)],
                       tmp_path / "out" / "p", _meta(), cover_src=cover)
    idx = (tmp_path / "out" / "p" / "index.html").read_text()
    assert 'const PACK_COVER = "cover.png"' in idx               # cover feeds the colour seed
    assert "getCover" in idx


def test_default_label_is_producer_text(tmp_path):
    packmod.build_pack([_track(tmp_path, "x.mp3", "B")], tmp_path / "out" / "p", _meta())
    idx = (tmp_path / "out" / "p" / "index.html").read_text()
    assert '<div class="label">Dibs</div>' in idx and "label cover" not in idx


def test_cover_art_becomes_the_vinyl_label(tmp_path):
    cover = tmp_path / "art.png"
    cover.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)  # png-ish bytes
    t = _track(tmp_path, "x.mp3", "B", bpm=140)
    packmod.build_pack([t], tmp_path / "out" / "p", _meta(), cover_src=cover)
    out = tmp_path / "out" / "p"
    assert (out / "cover.png").exists()                # cover copied into the pack
    idx = (out / "index.html").read_text()
    assert "label cover" in idx and "url('cover.png')" in idx
    assert ">Dibs</div>" not in idx                    # text label replaced by the art


def test_non_image_cover_is_ignored(tmp_path):
    notimg = tmp_path / "notes.txt"
    notimg.write_text("nope")
    packmod.build_pack([_track(tmp_path, "x.mp3", "B")], tmp_path / "out" / "p", _meta(), cover_src=notimg)
    out = tmp_path / "out" / "p"
    assert not (out / "cover.txt").exists()
    assert "label cover" not in (out / "index.html").read_text()  # falls back to text label


def test_turntable_engine_is_a_real_file():
    src = (DECK_DIR / "turntable.js").read_text(encoding="utf-8")
    assert "function ttRun" in src and "function ttScrub" in src
    assert packmod.TURNTABLE_JS == src  # constant now loads from the file


def test_dead_canvas_export_engine_removed():
    assert "ttExportFrame" not in packmod.TURNTABLE_JS
    assert "ttVinylScene" not in packmod.TURNTABLE_JS


def test_pack_inlines_shared_deck(tmp_path):
    out = tmp_path / "out" / "p"
    packmod.build_pack([_track(tmp_path, "x.mp3", "Beat", bpm=140)], out, _meta())
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "deck-module" in html and "__DECK_HTML__" not in html and "__DECK_CSS__" not in html
    # token-leak guard
    assert "__LABEL_HTML__" not in html and "__CLABEL_HTML__" not in html
    # pack's own :root sets --deck-size:330px and deck.css must NOT override it
    assert "--deck-size:330px" in html
    assert ":root{--deck-size" not in (DECK_DIR / "deck.css").read_text(encoding="utf-8")


def test_pack_is_themed_and_self_contained(tmp_path):
    out = tmp_path / "out" / "p"
    packmod.build_pack([_track(tmp_path, "x.mp3", "Beat", bpm=140)], out, _meta())
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'data-theme="editorial"' in html      # editorial by default
    assert "--accent:#d4aa5e" in html             # editorial accent colour inlined
    assert "http://" not in html and "https://" not in html  # monetize seam: fully static
    assert ".woff2" not in html                   # no font payload — self-contained


def test_pack_theme_flag(tmp_path):
    out = tmp_path / "out" / "classic"
    packmod.build_pack([_track(tmp_path, "x.mp3", "Beat", bpm=140)], out, _meta(), theme="classic")
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'data-theme="classic"' in html
