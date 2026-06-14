"""Ambition-tier: Claude-written captions, hashtags, and a one-line rationale
for each clip (feature-flagged).

Per the project spec this uses the Anthropic API with model claude-sonnet-4-6,
demands JSON output, and degrades gracefully: if the `anthropic` package isn't
installed or no API key is set, `is_available()` returns False and the caller
falls back to the plain captions stub. Nothing here runs unless `--ai` is passed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

MODEL = "claude-sonnet-4-6"  # project spec: Sonnet for cost on ambition-tier AI
MAX_TOKENS = 4096

# json_schema for structured output: every object needs additionalProperties:false
# and all properties required (Anthropic structured-output constraint).
_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "caption": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                },
                "required": ["index", "caption", "hashtags", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You write short-form social captions for a solo DJ/producer posting clips "
    "from their own set recordings. For each clip you get its position, source "
    "timestamp, length, an energy score (0..1, higher = bigger moment), and, "
    "when available, a transcript of what's audible. Write a punchy caption (no "
    "emoji spam, one tasteful emoji at most), 3-6 lowercase hashtags mixing reach "
    "and niche, and a one-line rationale for why the clip works. Match an "
    "underground electronic / amapiano / UK club voice — confident, not corny."
)


class AIError(RuntimeError):
    """An AI captioning call could not be completed."""


@dataclass(frozen=True)
class ClipCaption:
    index: int
    caption: str
    hashtags: list[str]
    rationale: str


def is_available() -> bool:
    """True only if the anthropic SDK is importable and an API key is set."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _build_user_prompt(clips: list[dict]) -> str:
    """clips: list of {index, timestamp, duration, score, transcript?}."""
    lines = ["Write captions for these clips. Return one entry per clip by index.\n"]
    for c in clips:
        lines.append(f"Clip {c['index']}:")
        lines.append(f"  source timestamp: {c.get('timestamp', '?')}")
        lines.append(f"  length: {c.get('duration', '?')}s")
        lines.append(f"  energy score: {c.get('score', '?')}")
        transcript = (c.get("transcript") or "").strip()
        lines.append(f"  transcript: {transcript or '(none)'}")
        lines.append("")
    return "\n".join(lines)


def _parse(text: str) -> list[ClipCaption]:
    try:
        data = json.loads(text)
        return [
            ClipCaption(
                index=int(c["index"]),
                caption=str(c["caption"]),
                hashtags=[str(h) for h in c["hashtags"]],
                rationale=str(c["rationale"]),
            )
            for c in data["clips"]
        ]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise AIError(f"could not parse AI response: {exc}") from exc


def caption_clips(clips: list[dict], client=None, model: str = MODEL) -> list[ClipCaption]:
    """Call Claude to caption each clip. `client` is injectable for testing.

    Raises AIError on any SDK/transport/parse failure so the caller can fall
    back to the plain captions stub without crashing the run.
    """
    if client is None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - guarded by is_available()
            raise AIError("anthropic package not installed") from exc
        client = anthropic.Anthropic()

    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=_SYSTEM,
            messages=[{"role": "user", "content": _build_user_prompt(clips)}],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )
    except Exception as exc:  # SDK raises many typed errors; degrade on all
        raise AIError(f"AI request failed: {exc}") from exc

    text = "".join(
        b.text for b in response.content if getattr(b, "type", None) == "text"
    )
    if not text:
        raise AIError("AI response had no text content")
    return _parse(text)
