"""Per-run outputs: manifest.csv and the captions.md stub."""

from __future__ import annotations

import csv
from pathlib import Path

from .analyze import Segment


def format_timestamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def write_manifest(out_dir: Path, source: Path, clips: list[tuple[Path, Segment]]) -> Path:
    path = out_dir / "manifest.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["clip", "source", "source_timestamp", "duration_s", "score"])
        for clip_path, seg in clips:
            writer.writerow(
                [
                    clip_path.name,
                    str(source),
                    format_timestamp(seg.start),
                    f"{seg.duration:.0f}",
                    f"{seg.score:.3f}",
                ]
            )
    return path


def write_captions_stub(out_dir: Path, clips: list[tuple[Path, Segment]]) -> Path:
    path = out_dir / "captions.md"
    lines = ["# Captions", ""]
    for clip_path, seg in clips:
        lines += [
            f"## {clip_path.name}",
            f"- source timestamp: {format_timestamp(seg.start)}",
            "- caption: ",
            "- hashtags: ",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
