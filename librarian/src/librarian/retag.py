"""Build a reviewable tag-repair plan: proposed Artist/Title/Genre values become
in-place ``TagEdit``s, filtered down to the fields that actually change.

The plan flows through the *same* apply/undo engine as a move plan — old values
are journaled, so undo restores them exactly. This module is deterministic; the
AI that proposes the tags lives in ai.py. A proposal can also be hand-written and
fed via ``--spec`` for a no-API, fully reviewable repair.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import tags
from .model import WRITABLE_TAGS, Plan, TagEdit


@dataclass
class TagProposal:
    """A proposed set of tag values for one file. ``confidence`` (high/medium/low)
    is informational — it's surfaced in the review so low-confidence rows are easy
    to untick; it never auto-filters anything."""

    path: Path
    fields: dict[str, str]
    reason: str = ""
    confidence: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "TagProposal":
        return cls(
            path=Path(d["path"]),
            fields={k: str(v) for k, v in (d.get("fields") or {}).items()},
            reason=d.get("reason", ""),
            confidence=d.get("confidence", ""),
        )


def build_retag_plan(
    library_root: Path,
    proposals: list[TagProposal],
    rekordbox_xml: Path | None = None,
) -> tuple[Plan, str]:
    """Turn proposals into a reviewable tag-repair Plan + report.

    Only writable fields with a non-empty value that actually *differs* from the
    file's current tag become a change — so the plan shows real repairs, not
    no-op rewrites. Files we can't read are skipped (left for manual attention).
    """
    root = library_root.absolute()
    edits: list[TagEdit] = []
    rows: list[tuple[Path, dict[str, tuple[str | None, str]], str]] = []

    for prop in proposals:
        fields = {
            k: v for k, v in prop.fields.items()
            if k in WRITABLE_TAGS and v and v.strip()
        }
        if not fields:
            continue
        try:
            current = tags.read_tags(prop.path, fields.keys())
        except tags.TagError:
            continue  # unreadable file — leave it alone
        changed = {f: v for f, v in fields.items() if v != (current.get(f) or "")}
        if not changed:
            continue
        reason = prop.reason or "tag repair"
        if prop.confidence:
            reason = f"{reason} (confidence: {prop.confidence})"
        edits.append(TagEdit(path=prop.path, fields=changed, reason=reason))
        rows.append((prop.path, {f: (current.get(f), v) for f, v in changed.items()}, prop.confidence))

    plan = Plan(library_root=root, actions=[], rekordbox_xml=rekordbox_xml, tag_edits=edits)
    return plan, _render_report(root, rows)


def _render_report(root: Path, rows: list) -> str:
    lines = ["# Tag repair plan", "", f"- {len(rows)} file(s) to retag", "", "## Changes"]
    if not rows:
        lines.append("- (nothing to change — tags already match)")
    for path, changed, conf in rows:
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path
        lines.append(f"- `{rel}`" + (f"  _(confidence: {conf})_" if conf else ""))
        for f, (old, new) in changed.items():
            lines.append(f"    - {f}: {old!r} → {new!r}")
    lines.append("")
    return "\n".join(lines)
