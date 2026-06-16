"""Ambition-tier: translate a plain-English organize request into an
``OrganizeSpec`` via Claude (feature-flagged).

Per the project spec this uses the Anthropic API with model claude-sonnet-4-6,
demands JSON output, and degrades gracefully: if the ``anthropic`` package isn't
installed or no API key is set, ``is_available()`` returns False and the caller
falls back (the deterministic ``cleanup`` is the non-AI equivalent). Nothing here
runs unless the user explicitly asks to organize by instruction.

Claude returns *rules*, never per-file paths or apply decisions — see organize.py
for why that split keeps every safety invariant intact. We send it a sample of
the library's filenames + the genres already present so its rules are grounded in
the real library (this is the one place track names leave the machine; it's
opt-in and uses the user's own API key).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .model import WRITABLE_TAGS
from .organize import ACTIONS, DEFAULTS, FIELDS, OPS, OrganizeError, OrganizeSpec

MODEL = "claude-sonnet-4-6"  # project spec: Sonnet for cost on ambition-tier AI
MAX_TOKENS = 2048

# Strict structured-output schema: every object sets additionalProperties:false
# and lists all properties as required (Anthropic structured-output constraint).
_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "default": {"type": "string", "enum": list(DEFAULTS)},
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "enum": list(FIELDS)},
                    "op": {"type": "string", "enum": list(OPS)},
                    "value": {"type": "string"},
                    "action": {"type": "string", "enum": list(ACTIONS)},
                    "target": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["field", "op", "value", "action", "target", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "default", "rules"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You turn a DJ's plain-English request for organising their music library "
    "into a small, ordered set of rules. Rules are matched top-to-bottom; the "
    "first match wins, and unmatched files fall to `default`.\n\n"
    "Each rule matches one FIELD with one OP:\n"
    "  fields: artist, title, genre, filename, extension, quality\n"
    "  ops: contains, equals, startswith, endswith, is_low_quality, any\n"
    "and takes one ACTION:\n"
    "  folder (move into the folder named by `target`), by_genre (file into a "
    "folder named after the track's genre tag), rename_artist_title (rename to "
    "'Artist - Title' from tags), quarantine (set aside — never deletes).\n"
    "`default` (for unmatched files): leave, by_genre, or rename_artist_title.\n\n"
    "Rules of thumb: use `quality is_low_quality` for 'low quality / bad rips'; "
    "use `filename contains/endswith` for download-site markers like "
    "'_spotdown.org'; prefer the genres that already exist in the library; keep "
    "each `reason` short and human (it shows in a review table). You ONLY organise "
    "by name, genre, extension and quality — never claim to analyse audio, and "
    "never touch musical key or BPM. Set `summary` to one sentence paraphrasing "
    "how you read the request, so the user can sanity-check it."
)


class AIError(RuntimeError):
    """An AI organize-spec call could not be completed."""


def is_available() -> bool:
    """True only if the anthropic SDK is importable and an API key is set."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _user_prompt(instruction: str, sample_names: list[str], genres: list[str]) -> str:
    lines = [f"Request: {instruction}", ""]
    if genres:
        lines.append("Genres already present in the library: " + ", ".join(genres))
    if sample_names:
        lines.append("\nA sample of the library's filenames:")
        lines += [f"  {n}" for n in sample_names]
    lines.append("\nReturn the rule-set as JSON.")
    return "\n".join(lines)


def infer_spec(
    instruction: str,
    *,
    sample_names: list[str] | None = None,
    genres: list[str] | None = None,
    client=None,
    model: str = MODEL,
) -> OrganizeSpec:
    """Ask Claude for an OrganizeSpec. ``client`` is injectable for testing.

    Raises AIError on any SDK/transport/parse/validation failure so the caller can
    surface a clean message instead of crashing.
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
            messages=[
                {
                    "role": "user",
                    "content": _user_prompt(
                        instruction, sample_names or [], genres or []
                    ),
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )
    except Exception as exc:  # SDK raises many typed errors; degrade on all
        raise AIError(f"AI request failed: {exc}") from exc

    text = "".join(
        b.text for b in response.content if getattr(b, "type", None) == "text"
    )
    if not text:
        raise AIError("AI response had no text content")
    try:
        return OrganizeSpec.from_dict(json.loads(text))
    except (json.JSONDecodeError, KeyError, TypeError, OrganizeError) as exc:
        raise AIError(f"could not parse AI response: {exc}") from exc


# --- tag repair (metadata) ------------------------------------------------

_TAGS_SCHEMA = {
    "type": "object",
    "properties": {
        "tracks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "artist": {"type": "string"},
                    "title": {"type": "string"},
                    "genre": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "note": {"type": "string"},
                },
                "required": ["index", "artist", "title", "genre", "confidence", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tracks"],
    "additionalProperties": False,
}

_TAGS_SYSTEM = (
    "You repair music-library metadata for a DJ. For each track you get its "
    "filename and current artist/title/genre tags. Propose clean values:\n"
    "  - Parse 'Artist - Title (Version)' filenames; pull featured artists out of "
    "the artist field ('ft'/'feat'); keep edit/version markers (Intro, Clean, "
    "Extended, Remix) in the TITLE only if they're part of the release, otherwise "
    "drop download-site junk.\n"
    "  - Leave a field as an EMPTY STRING when the current tag is already fine or "
    "you can't confidently improve it. Leave GENRE empty unless you're genuinely "
    "sure — never guess a genre.\n"
    "  - NEVER output or alter musical key or BPM; you only touch artist/title/"
    "genre.\n"
    "Give each track a confidence (high/medium/low) and a one-line note. Return "
    "exactly one entry per track, keyed by the index you were given."
)


@dataclass(frozen=True)
class TagSuggestion:
    """A per-file tag proposal from Claude: ``fields`` holds only the non-empty
    proposed values among artist/title/genre."""

    index: int
    fields: dict
    confidence: str
    note: str


def _tags_user_prompt(tracks: list[dict], instruction: str | None) -> str:
    lines = []
    if instruction and instruction.strip():
        lines.append(f"Extra guidance from the user: {instruction.strip()}\n")
    lines.append("Propose clean tags for these tracks:")
    for t in tracks:
        lines.append(f"\nTrack {t['index']}:")
        lines.append(f"  filename: {t.get('filename', '?')}")
        lines.append(f"  current artist: {t.get('artist') or '(none)'}")
        lines.append(f"  current title:  {t.get('title') or '(none)'}")
        lines.append(f"  current genre:  {t.get('genre') or '(none)'}")
    return "\n".join(lines)


def propose_tags(
    tracks: list[dict],
    *,
    instruction: str | None = None,
    client=None,
    model: str = MODEL,
) -> list[TagSuggestion]:
    """Ask Claude to propose clean artist/title/genre per track. ``tracks`` is a
    list of {index, filename, artist, title, genre}. ``client`` is injectable for
    testing. Raises AIError on any failure so the caller degrades cleanly."""
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
            system=_TAGS_SYSTEM,
            messages=[{"role": "user", "content": _tags_user_prompt(tracks, instruction)}],
            output_config={"format": {"type": "json_schema", "schema": _TAGS_SCHEMA}},
        )
    except Exception as exc:
        raise AIError(f"AI request failed: {exc}") from exc

    text = "".join(
        b.text for b in response.content if getattr(b, "type", None) == "text"
    )
    if not text:
        raise AIError("AI response had no text content")
    try:
        data = json.loads(text)
        out: list[TagSuggestion] = []
        for t in data["tracks"]:
            fields = {
                k: str(t[k]).strip()
                for k in WRITABLE_TAGS
                if t.get(k) and str(t[k]).strip()
            }
            out.append(
                TagSuggestion(
                    index=int(t["index"]),
                    fields=fields,
                    confidence=str(t["confidence"]),
                    note=str(t.get("note", "")),
                )
            )
        return out
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise AIError(f"could not parse AI response: {exc}") from exc
