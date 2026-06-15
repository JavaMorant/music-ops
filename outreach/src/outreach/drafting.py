"""Turn a contact into a personalised email draft — written to a file for the
user to review, edit, and send by hand. **This never sends.**

A template is plain markdown with a leading ``Subject:`` line and ``{{ slot }}``
placeholders. Slots the contact data can fill (name, org, genre_fit, plus the
sender profile's ``me.*`` fields) are substituted in. Slots we genuinely don't
know are replaced with a visible ``[[ FILL: slot ]]`` marker rather than guessed
— and templates also carry literal ``[[ FILL: ... ]]`` prompts for the specific,
researched detail every good cold email needs (their recent night, why you fit).
That keeps the tool honest: it personalises from real data and flags the rest.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .model import Contact, PipelineState, Touch

TEMPLATES_DIR = Path(__file__).parent / "templates"
DEFAULT_TEMPLATE = "cold"

_SLOT = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
_FILL = "[[ FILL: {name} ]]"


class DraftError(RuntimeError):
    """A draft could not be rendered (missing template, etc.)."""


@dataclass
class RenderedDraft:
    subject: str
    body: str
    unfilled: list[str] = field(default_factory=list)  # slot names left as FILL

    @property
    def text(self) -> str:
        return f"Subject: {self.subject}\n\n{self.body}".rstrip() + "\n"


def resolve_template(name_or_path: str) -> Path:
    """Resolve a template by bare name (a packaged template) or by file path."""
    p = Path(name_or_path)
    if p.suffix and p.exists():
        return p
    packaged = TEMPLATES_DIR / f"{name_or_path}.md"
    if packaged.exists():
        return packaged
    available = ", ".join(sorted(t.stem for t in TEMPLATES_DIR.glob("*.md")))
    raise DraftError(
        f"template {name_or_path!r} not found (packaged: {available}; or pass a path)"
    )


def load_profile(profile_path: Path | None) -> dict:
    """Load the sender profile TOML (``[me]`` table) into a flat ``me.*`` context.

    Absent or partial is fine — any ``me.*`` slot with no value becomes a FILL
    marker so the user notices, rather than the draft going out half-built.
    """
    if not profile_path:
        return {}
    if not profile_path.exists():
        raise DraftError(f"profile {profile_path} not found")
    data = tomllib.loads(profile_path.read_text(encoding="utf-8"))
    me = data.get("me", data)
    out: dict[str, str] = {}
    for key, value in me.items():
        if isinstance(value, (list, tuple)):
            out[f"me.{key}"] = "\n".join(str(v) for v in value)
        else:
            out[f"me.{key}"] = str(value)
    return out


def _first_name(name: str) -> str:
    return name.strip().split()[0] if name and name.strip() else name


def build_context(
    contact: Contact,
    state: PipelineState | None = None,
    touches: list[Touch] | None = None,
    profile: dict | None = None,
    today: date | None = None,
) -> dict[str, str]:
    """Flatten everything the templates can reference into a {slot: value} map.

    Only fields that are actually populated land in the context; missing ones are
    left out so they surface as FILL markers at render time.
    """
    day = today or date.today()
    ctx: dict[str, str] = {
        "name": contact.name,
        "first_name": _first_name(contact.name),
        "date": day.isoformat(),
    }
    for k in ("org", "role", "city", "genre_fit", "source", "notes"):
        v = getattr(contact, k)
        if v:
            ctx[k] = str(v)
    if contact.venue_capacity is not None:
        ctx["venue_capacity"] = str(contact.venue_capacity)
    if state is not None:
        ctx["stage"] = state.stage
        if state.last_touch:
            ctx["last_touch"] = state.last_touch
    if touches:
        ctx["last_note"] = touches[0].note
    if profile:
        ctx.update(profile)
    return ctx


def render(template_text: str, context: dict[str, str]) -> RenderedDraft:
    """Substitute ``{{ slot }}`` placeholders; unknown/empty slots become
    ``[[ FILL: slot ]]`` markers and are reported in ``unfilled``."""
    unfilled: list[str] = []

    def sub(match: re.Match) -> str:
        slot = match.group(1)
        value = context.get(slot)
        if value is None or value == "":
            if slot not in unfilled:
                unfilled.append(slot)
            return _FILL.format(name=slot)
        return value

    filled = _SLOT.sub(sub, template_text)

    subject = ""
    body = filled
    lines = filled.splitlines()
    if lines and lines[0].lower().startswith("subject:"):
        subject = lines[0].split(":", 1)[1].strip()
        body = "\n".join(lines[1:]).lstrip("\n")

    # literal [[ FILL: ... ]] prompts baked into the template count as unfilled too
    for literal in re.findall(r"\[\[ FILL: (.+?) \]\]", body):
        if literal not in unfilled:
            unfilled.append(literal)

    return RenderedDraft(subject=subject, body=body, unfilled=unfilled)


def render_draft(
    contact: Contact,
    *,
    template: str = DEFAULT_TEMPLATE,
    state: PipelineState | None = None,
    touches: list[Touch] | None = None,
    profile: dict | None = None,
    today: date | None = None,
) -> RenderedDraft:
    """High-level: resolve the template, build context, render."""
    template_path = resolve_template(template)
    ctx = build_context(contact, state, touches, profile, today)
    return render(template_path.read_text(encoding="utf-8"), ctx)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "contact"


def write_draft(
    draft: RenderedDraft,
    out_dir: Path,
    contact: Contact,
    template: str,
    today: date | None = None,
) -> Path:
    """Write the rendered draft to ``out_dir`` and return its path. Files are
    named ``<date>-<contact-slug>-<template>.md`` and never overwritten — a
    numeric suffix is added if one already exists."""
    day = today or date.today()
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"{day.isoformat()}-{_slug(contact.name)}-{_slug(template)}"
    dest = out_dir / f"{base}.md"
    n = 2
    while dest.exists():
        dest = out_dir / f"{base}-{n}.md"
        n += 1
    dest.write_text(draft.text, encoding="utf-8")
    return dest
