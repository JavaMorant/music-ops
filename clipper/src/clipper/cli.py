"""clipper CLI — analyze set recordings, cut top-energy clips, write manifests."""

from __future__ import annotations

import datetime
import tempfile
from pathlib import Path
from typing import Annotated, Optional

import typer

from . import media
from .analyze import score_audio, select_segments
from .manifest import format_timestamp, write_captions_stub, write_manifest

app = typer.Typer(
    help="Turn long set recordings into ready-to-post short clips.",
    no_args_is_help=True,
)


def _scored_audio(source: Path):
    """Extract the audio track (long-file rule), score it, clean up."""
    with tempfile.TemporaryDirectory(prefix="clipper-") as tmp:
        typer.echo(f"Extracting audio from {source.name} …")
        wav = media.extract_audio(source, Path(tmp))
        typer.echo("Scoring energy + onsets …")
        return score_audio(wav)


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


def _validate_x_offset(frame: tuple[int, int], x_offset: int) -> None:
    width, height = frame
    crop_w = round(height * 9 / 16)
    limit = width - crop_w
    if not 0 <= x_offset <= limit:
        typer.secho(
            f"--x-offset {x_offset} out of range: source is {width}x{height}, "
            f"crop window is {crop_w}px wide, so offset must be 0..{limit}",
            fg="red",
            err=True,
        )
        raise typer.Exit(1)


@app.command()
def analyze(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Set recording (video or audio)")],
    length: Annotated[int, typer.Option("--len", min=15, max=60, help="Candidate clip length in seconds")] = 30,
    spacing: Annotated[int, typer.Option(min=0, help="Min seconds between candidates")] = 60,
    top: Annotated[int, typer.Option(min=1, help="How many candidates to show")] = 10,
) -> None:
    """Rank the highest-energy moments of a set recording."""
    try:
        scores, duration = _scored_audio(source)
        segments = select_segments(scores, clip_len=length, max_clips=top, spacing=spacing)
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
    x_offset: Annotated[Optional[int], typer.Option(help="Manual crop x-offset in px (default: centre)")] = None,
    out: Annotated[Optional[Path], typer.Option(help="Output dir (default out/<date>/)")] = None,
) -> None:
    """Cut the top-N highest-energy segments to 9:16 clips + manifest."""
    from .cut import cut_audio_segment, cut_segment  # deferred with the rest

    try:
        frame = media.video_frame_size(source)
        if frame is not None and x_offset is not None:
            _validate_x_offset(frame, x_offset)
        scores, _ = _scored_audio(source)
        segments = select_segments(scores, clip_len=length, max_clips=clips, spacing=spacing)
        if not segments:
            typer.secho("No segments found — source too short?", fg="red", err=True)
            raise typer.Exit(1)

        out_dir = _fresh_out_dir(out)

        results = []
        ext = ".mp4" if frame is not None else source.suffix.lower()
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

    manifest = write_manifest(out_dir, source, results)
    captions = write_captions_stub(out_dir, results)
    typer.echo(f"\n{len(results)} clips → {out_dir}/")
    typer.echo(f"Manifest: {manifest}")
    typer.echo(f"Captions stub: {captions}")


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
