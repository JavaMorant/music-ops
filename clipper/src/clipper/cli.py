"""clipper CLI — analyze set recordings, cut top-energy clips, write manifests."""

from __future__ import annotations

import datetime
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
    """Extract the audio track (long-file rule) and score it."""
    typer.echo(f"Extracting audio from {source.name} …")
    wav = media.extract_audio(source)
    typer.echo("Scoring energy + onsets …")
    return score_audio(wav)


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
        is_video = media.has_video_stream(source)
        scores, _ = _scored_audio(source)
        segments = select_segments(scores, clip_len=length, max_clips=clips, spacing=spacing)
        if not segments:
            typer.secho("No segments found — source too short?", fg="red", err=True)
            raise typer.Exit(1)

        out_dir = out or Path("out") / datetime.date.today().isoformat()
        out_dir.mkdir(parents=True, exist_ok=True)

        results = []
        ext = ".mp4" if is_video else source.suffix.lower()
        for i, seg in enumerate(segments, 1):
            dest = out_dir / f"clip_{i:02d}{ext}"
            typer.echo(
                f"Cutting clip {i}/{len(segments)} @ {format_timestamp(seg.start)} (score {seg.score:.3f}) …"
            )
            if is_video:
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


if __name__ == "__main__":
    app()
