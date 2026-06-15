"""clipper CLI — analyze set recordings, cut top-energy clips, write manifests."""

from __future__ import annotations

import datetime
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Optional

import typer

from . import media
from .analyze import (
    BUILD_WINDOW,
    CROWD_WEIGHT,
    DROP_WEIGHT,
    LEAD_IN_SECONDS,
    align_to_beats,
    blend_visual,
    score_audio,
    select_segments,
)
from .manifest import format_timestamp, write_captions, write_manifest

app = typer.Typer(
    help="Turn long set recordings into ready-to-post short clips.",
    no_args_is_help=True,
)


@contextmanager
def _audio_workspace(source: Path, want_crowd: bool = False):
    """Extract the audio track once (long-file rule) and yield (wav, scores,
    crowd, duration). The wav stays available for beat alignment too; the temp
    dir is cleaned on exit. Video is never decoded here — only at cut time.
    `crowd` is an empty array unless `want_crowd` is set.
    """
    with tempfile.TemporaryDirectory(prefix="clipper-") as tmp:
        typer.echo(f"Extracting audio from {source.name} …")
        wav = media.extract_audio(source, Path(tmp))
        typer.echo("Scoring energy + onsets …")
        scores, crowd, duration = score_audio(wav, want_crowd=want_crowd)
        yield wav, scores, crowd, duration


def _fresh_out_dir(out: Optional[Path]) -> Path:
    """out/<date>/, suffixed with the time if that run already exists."""
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        return out
    base = Path("out") / datetime.date.today().isoformat()
    out_dir = base
    if (out_dir / "manifest.csv").exists():
        out_dir = base.parent / f"{base.name}-{datetime.datetime.now():%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _maybe_transcribe(wav: Path):
    """Transcribe the wav if faster-whisper is available; warn and skip otherwise."""
    from . import transcribe as tx

    if not tx.is_available():
        typer.secho(
            "--transcribe needs faster-whisper (pip install 'clipper[ai]'); skipping.",
            fg="yellow",
            err=True,
        )
        return []
    typer.echo("Transcribing (Whisper) …")
    try:
        return tx.transcribe(wav)
    except tx.TranscribeError as exc:
        typer.secho(f"transcription failed, continuing without it: {exc}", fg="yellow", err=True)
        return []


def _clip_transcripts(results, transcript_segments) -> dict[int, str]:
    """Map each clip number (1..N) to the transcript text over its time window."""
    if not transcript_segments:
        return {}
    from .transcribe import transcript_for_window

    out = {}
    for i, (_dest, seg) in enumerate(results, 1):
        text = transcript_for_window(transcript_segments, seg.start, seg.end)
        if text:
            out[i] = text
    return out


def _maybe_ai_captions(results, transcripts: dict[int, str]) -> dict:
    """Draft captions with Claude if available; warn and return {} otherwise."""
    from . import ai

    if not ai.is_available():
        typer.secho(
            "--ai needs the anthropic package and ANTHROPIC_API_KEY; skipping.",
            fg="yellow",
            err=True,
        )
        return {}
    clips = [
        {
            "index": i,
            "timestamp": format_timestamp(seg.start),
            "duration": int(seg.duration),
            "score": round(seg.score, 3),
            "transcript": transcripts.get(i, ""),
        }
        for i, (_dest, seg) in enumerate(results, 1)
    ]
    typer.echo("Drafting captions (Claude) …")
    try:
        return {c.index: c for c in ai.caption_clips(clips)}
    except ai.AIError as exc:
        typer.secho(f"AI captioning failed, continuing without it: {exc}", fg="yellow", err=True)
        return {}


def _output_ext(frame: Optional[tuple[int, int]]) -> str:
    """Video sources → .mp4; audio-only → .m4a (AAC). Never reuse the raw source
    extension, so a .wav set recording yields a compact, postable clip."""
    return ".mp4" if frame is not None else ".m4a"


def _validate_x_offset(frame: tuple[int, int], x_offset: int) -> None:
    width, height = frame
    # Clamp to the source: a source already 9:16 or taller crops to its full
    # width, so the valid offset range collapses to {0} rather than going negative.
    # Floor-divide to match ffmpeg's crop truncation (int(ih*9/16)) exactly, so
    # the reported limit is the true headroom, not 1px over on odd dimensions.
    crop_w = min(width, height * 9 // 16)
    limit = width - crop_w
    if not 0 <= x_offset <= limit:
        if limit == 0:
            msg = (
                f"--x-offset has no effect: source {width}x{height} is already "
                "9:16 or taller, so the crop spans the full width (use 0)"
            )
        else:
            msg = (
                f"--x-offset {x_offset} out of range: source is {width}x{height}, "
                f"crop window is {crop_w}px wide, so offset must be 0..{limit}"
            )
        typer.secho(msg, fg="red", err=True)
        raise typer.Exit(1)


@app.command()
def analyze(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Set recording (video or audio)")],
    length: Annotated[int, typer.Option("--len", min=15, max=60, help="Candidate clip length in seconds")] = 30,
    spacing: Annotated[int, typer.Option(min=0, help="Min seconds between candidates")] = 60,
    top: Annotated[int, typer.Option(min=1, help="How many candidates to show")] = 10,
    drop_weight: Annotated[float, typer.Option("--drop-weight", min=0.0, max=1.0, help="Selection blend: 0 = sustained loudness, 1 = sharp drops")] = DROP_WEIGHT,
    lead_in: Annotated[int, typer.Option("--lead-in", min=0, help="Start each candidate this many seconds before the detected drop")] = LEAD_IN_SECONDS,
    build_window: Annotated[int, typer.Option("--build-window", min=1, help="Seconds compared before/after a moment to measure the energy step up")] = BUILD_WINDOW,
    crowd_weight: Annotated[float, typer.Option("--crowd-weight", min=0.0, max=1.0, help="Reward crowd-roar moments: 0 = off, try 0.4 (extra audio analysis)")] = CROWD_WEIGHT,
    beat_align: Annotated[bool, typer.Option("--beat-align/--no-beat-align", help="Snap candidate starts to the nearest beat")] = True,
    visual: Annotated[bool, typer.Option("--visual/--no-visual", help="Blend on-camera motion/flash energy into scoring (extra video pass)")] = False,
) -> None:
    """Rank the highest-energy moments of a set recording."""
    try:
        with _audio_workspace(source, want_crowd=crowd_weight > 0) as (wav, scores, crowd, duration):
            if visual and media.video_frame_size(source) is not None:
                typer.echo("Analyzing visual activity …")
                scores = blend_visual(scores, media.visual_activity(source, wav.parent))
            segments = select_segments(
                scores,
                clip_len=length,
                max_clips=top,
                spacing=spacing,
                lead_in=lead_in,
                drop_weight=drop_weight,
                build_window=build_window,
                crowd=crowd,
                crowd_weight=crowd_weight,
            )
            if beat_align:
                typer.echo("Aligning to beats …")
                segments = align_to_beats(wav, segments, duration)
    except media.MediaError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1)

    typer.echo(f"\n{source.name} — {format_timestamp(duration)} total")
    typer.echo(f"{'#':>2}  {'start':>8}  {'end':>8}  score")
    for i, seg in enumerate(segments, 1):
        typer.echo(
            f"{i:>2}  {format_timestamp(seg.start):>8}  {format_timestamp(seg.end):>8}  {seg.score:.3f}"
        )


@app.command()
def cut(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Set recording (video or audio)")],
    clips: Annotated[int, typer.Option(min=1, help="Number of clips to cut")] = 5,
    length: Annotated[int, typer.Option("--len", min=15, max=60, help="Clip length in seconds")] = 30,
    spacing: Annotated[int, typer.Option(min=0, help="Min seconds between clips")] = 60,
    drop_weight: Annotated[float, typer.Option("--drop-weight", min=0.0, max=1.0, help="Selection blend: 0 = sustained loudness, 1 = sharp drops")] = DROP_WEIGHT,
    lead_in: Annotated[int, typer.Option("--lead-in", min=0, help="Start each clip this many seconds before the detected drop")] = LEAD_IN_SECONDS,
    build_window: Annotated[int, typer.Option("--build-window", min=1, help="Seconds compared before/after a moment to measure the energy step up")] = BUILD_WINDOW,
    crowd_weight: Annotated[float, typer.Option("--crowd-weight", min=0.0, max=1.0, help="Reward crowd-roar moments: 0 = off, try 0.4 (extra audio analysis)")] = CROWD_WEIGHT,
    x_offset: Annotated[Optional[int], typer.Option(help="Manual crop x-offset in px (default: centre)")] = None,
    out: Annotated[Optional[Path], typer.Option(help="Output dir (default out/<date>/)")] = None,
    beat_align: Annotated[bool, typer.Option("--beat-align/--no-beat-align", help="Snap clip starts to the nearest beat")] = True,
    visual: Annotated[bool, typer.Option("--visual/--no-visual", help="Blend on-camera motion/flash energy into scoring (extra video pass)")] = False,
    transcribe: Annotated[bool, typer.Option("--transcribe", help="Transcribe with Whisper for per-clip captions (needs faster-whisper)")] = False,
    ai: Annotated[bool, typer.Option("--ai", help="Draft captions/hashtags with Claude (needs anthropic + ANTHROPIC_API_KEY)")] = False,
) -> None:
    """Cut the top-N highest-energy segments to 9:16 clips + manifest."""
    from .cut import cut_audio_segment, cut_segment  # deferred with the rest

    transcript_segments = []
    try:
        frame = media.video_frame_size(source)
        if frame is not None and x_offset is not None:
            _validate_x_offset(frame, x_offset)
        with _audio_workspace(source, want_crowd=crowd_weight > 0) as (wav, scores, crowd, duration):
            if visual and frame is not None:
                typer.echo("Analyzing visual activity …")
                scores = blend_visual(scores, media.visual_activity(source, wav.parent))
            segments = select_segments(
                scores,
                clip_len=length,
                max_clips=clips,
                spacing=spacing,
                lead_in=lead_in,
                drop_weight=drop_weight,
                build_window=build_window,
                crowd=crowd,
                crowd_weight=crowd_weight,
            )
            if not segments:
                typer.secho("No segments found — source too short?", fg="red", err=True)
                raise typer.Exit(1)
            if beat_align:
                typer.echo("Aligning to beats …")
                segments = align_to_beats(wav, segments, duration)
            if transcribe:
                transcript_segments = _maybe_transcribe(wav)

        out_dir = _fresh_out_dir(out)

        results = []
        ext = _output_ext(frame)
        for i, seg in enumerate(segments, 1):
            dest = out_dir / f"clip_{i:02d}{ext}"
            typer.echo(
                f"Cutting clip {i}/{len(segments)} @ {format_timestamp(seg.start)} (score {seg.score:.3f}) …"
            )
            if frame is not None:
                cut_segment(source, seg, dest, x_offset=x_offset)
            else:
                cut_audio_segment(source, seg, dest)
            results.append((dest, seg))
    except media.MediaError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1)

    transcripts = _clip_transcripts(results, transcript_segments)
    captions = _maybe_ai_captions(results, transcripts) if ai else {}

    manifest = write_manifest(out_dir, source, results)
    captions_path = write_captions(out_dir, results, captions=captions, transcripts=transcripts)
    typer.echo(f"\n{len(results)} clips → {out_dir}/")
    typer.echo(f"Manifest: {manifest}")
    typer.echo(f"Captions: {captions_path}")


@app.command()
def overlay(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Clip to burn text into")],
    event: Annotated[str, typer.Option(help="Event name")] = "",
    date: Annotated[str, typer.Option(help="Event date")] = "",
    artist: Annotated[str, typer.Option(help="Artist name")] = "",
    template: Annotated[Optional[Path], typer.Option(exists=True, dir_okay=False, help="TOML overlay template")] = None,
    out: Annotated[Optional[Path], typer.Option(help="Output file (default: <clip>_overlay.mp4)")] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing output file")] = False,
) -> None:
    """Burn an event/date/artist text overlay into a clip."""
    from .overlay import build_filter, burn_overlay, load_template

    dest = out or source.with_name(f"{source.stem}_overlay.mp4")
    if dest.exists() and not force:
        typer.secho(f"{dest} exists — pass --force to overwrite", fg="red", err=True)
        raise typer.Exit(1)
    try:
        style, lines = load_template(template)
        with tempfile.TemporaryDirectory(prefix="clipper-overlay-") as tmp:
            vf = build_filter(
                style, lines, {"event": event, "date": date, "artist": artist}, Path(tmp)
            )
            typer.echo(f"Burning overlay into {source.name} …")
            burn_overlay(source, dest, vf)
    except media.MediaError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1)
    typer.echo(f"→ {dest}")


@app.command()
def artwork(
    image: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Source artwork image")],
    out: Annotated[Optional[Path], typer.Option(help="Output dir (default out/artwork/<name>/)")] = None,
) -> None:
    """Batch-resize one artwork into every platform spec."""
    from .artwork import ArtworkError, render_specs

    out_dir = out or Path("out") / "artwork" / image.stem
    try:
        results = render_specs(image, out_dir)
    except ArtworkError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1)
    for name, dest, upscaled in results:
        note = "  (upscaled — source smaller than spec)" if upscaled else ""
        typer.echo(f"{name:>8}: {dest}{note}")


if __name__ == "__main__":
    app()
