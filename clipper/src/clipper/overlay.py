"""Burn a text overlay (event / date / artist) into a clip via drawtext.

Templates live in a small TOML file: a [style] table plus [[line]] entries
whose text contains {event} / {date} / {artist} slots. Video re-encodes
(drawtext requires it); audio is stream-copied — never more re-encoding
than needed.

Text reaches ffmpeg via per-line textfile=, never inline: drawtext's
two-level escaping cannot safely carry ':' or '%' from user values, and a
colon would otherwise inject drawtext options. Style values from the
user-editable template are validated against safe charsets for the same
reason.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

from .media import MediaError

DEFAULT_STYLE = {
    "font": "/System/Library/Fonts/Helvetica.ttc",
    "color": "white",
    "border": 3,
    "border_color": "black@0.6",
}

DEFAULT_LINES = [
    {"text": "{event}", "size": 72, "y": "h*0.78"},
    {"text": "{date} · {artist}", "size": 40, "y": "h*0.85"},
]

_COLOR_RE = re.compile(r"^[A-Za-z0-9#@.]+$")  # white, black@0.6, #rrggbb, 0xRRGGBB@0.5
_Y_EXPR_RE = re.compile(r"^[0-9hwt+\-*/.() ]+$")
_PATH_SPECIALS = "\\':,;[]="  # filtergraph/option parser specials in file paths


def _escape_path(path: Path) -> str:
    s = str(path)
    for ch in _PATH_SPECIALS:
        s = s.replace(ch, "\\" + ch)
    return s


class _BlankMissing(dict):
    def __missing__(self, key: str) -> str:
        return ""


def load_template(path: Path | None) -> tuple[dict, list[dict]]:
    """Return validated (style, lines) from a TOML template or defaults."""
    if path is None:
        style, lines = dict(DEFAULT_STYLE), [dict(d) for d in DEFAULT_LINES]
    else:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise MediaError(f"bad overlay template {path.name}: {exc}") from exc
        style = {**DEFAULT_STYLE, **data.get("style", {})}
        lines = [dict(d) for d in data.get("line", DEFAULT_LINES)]

    for key in ("color", "border_color"):
        if not isinstance(style[key], str) or not _COLOR_RE.match(style[key]):
            raise MediaError(f"overlay template: bad {key} {style[key]!r}")
    try:
        style["border"] = int(style["border"])
    except (TypeError, ValueError):
        raise MediaError("overlay template: border must be an integer")
    if not isinstance(style["font"], str) or not Path(style["font"]).is_file():
        raise MediaError(f"overlay template: font file not found: {style['font']!r}")

    for line in lines:
        if not isinstance(line.get("text"), str):
            raise MediaError("overlay template: every [[line]] needs a string 'text'")
        try:
            line["size"] = int(line.get("size", 48))
        except (TypeError, ValueError):
            raise MediaError(f"overlay template: bad size in line {line['text']!r}")
        y = line.get("y", "h*0.8")
        if not isinstance(y, str) or not _Y_EXPR_RE.match(y):
            raise MediaError(f"overlay template: bad y expression {y!r}")
        line["y"] = y
    return style, lines


def build_filter(style: dict, lines: list[dict], slots: dict[str, str], workdir: Path) -> str:
    """Render line templates into a drawtext chain, writing each line's text
    to a file under workdir (caller owns workdir's lifetime — the files must
    survive until ffmpeg has run). Lines with no rendered text are dropped.
    """
    parts = []
    for i, line in enumerate(lines):
        text = line["text"].format_map(_BlankMissing(slots)).strip(" ·-–")
        if not text:
            continue
        textfile = workdir / f"line{i}.txt"
        textfile.write_text(text, encoding="utf-8")
        parts.append(
            "drawtext="
            "expansion=none"  # textfile content is literal: no %{} functions
            f":fontfile={_escape_path(Path(style['font']))}"
            f":textfile={_escape_path(textfile)}"
            f":fontsize={line['size']}"
            f":fontcolor={style['color']}"
            f":borderw={style['border']}"
            f":bordercolor={style['border_color']}"
            f":x=(w-text_w)/2"
            f":y={line['y']}"
        )
    if not parts:
        raise MediaError("overlay has no text to draw — pass --event/--date/--artist")
    return ",".join(parts)


def burn_overlay(source: Path, dest: Path, vf: str) -> None:
    if dest.resolve() == source.resolve():
        raise MediaError(f"refusing to overwrite source file: {source}")
    args = [
        "ffmpeg",
        "-v", "error",
        "-i", str(source),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-y",
        str(dest),
    ]
    try:
        subprocess.run(args, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise MediaError(
            f"ffmpeg failed overlaying {dest.name}:\n{exc.stderr.strip()[-2000:]}"
        ) from exc
