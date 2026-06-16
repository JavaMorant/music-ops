"""Ambition-tier: turn a natural-language request into a reviewable organize plan.

The split that keeps this safe: Claude does ONLY the fuzzy translation —
instruction → a small, ordered ``OrganizeSpec`` (a list of rules). This module
applies those rules **deterministically** over the scanned library to build the
same ``model.Plan`` the engine already knows how to preflight, journal, apply and
undo. The AI never sees a file path it can act on and never touches the apply
path: it proposes rules, the deterministic engine + the human review table decide
and execute. Every safety invariant (dry-run, never-delete via quarantine,
never-clobber, undo) therefore holds unchanged — this is just a richer way to
*author* a plan, never a new way to mutate the library.

Rules are matched top-to-bottom; the first rule that matches a file wins, and
files no rule matches fall to ``spec.default``. Matching is intentionally small
and safe — substring/equality over a few known fields, no regex (no ReDoS), and
nothing that pretends to analyse audio (musical key/BPM are off-limits here).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .metadata import TrackMeta, read_meta
from .model import MOVE, QUARANTINE, Action, Plan
from .naming import target_stem
from .paths import audio_files, collision_free, norm_key, quarantine_dest, sanitize_component

FIELDS = ("artist", "title", "genre", "filename", "extension", "quality")
OPS = ("contains", "equals", "startswith", "endswith", "is_low_quality", "any")
ACTIONS = ("folder", "quarantine", "rename_artist_title", "by_genre")
DEFAULTS = ("leave", "by_genre", "rename_artist_title")


class OrganizeError(RuntimeError):
    """An organize spec was malformed (unknown field/op/action/default)."""


@dataclass(frozen=True)
class Rule:
    """One ``match → action`` rule. ``target`` names the folder for ``folder``
    actions (ignored otherwise); ``reason`` is the human note shown in the review
    table (a sensible default is filled in when blank)."""

    field: str
    op: str
    value: str = ""
    action: str = "folder"
    target: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if self.field not in FIELDS:
            raise OrganizeError(f"unknown match field {self.field!r}; valid: {', '.join(FIELDS)}")
        if self.op not in OPS:
            raise OrganizeError(f"unknown op {self.op!r}; valid: {', '.join(OPS)}")
        if self.action not in ACTIONS:
            raise OrganizeError(f"unknown action {self.action!r}; valid: {', '.join(ACTIONS)}")
        # A text op with no needle would silently match nothing — reject it so the
        # mistake surfaces at spec time, not as a plan that mysteriously does less
        # than asked. (`any` and `is_low_quality` legitimately carry no value.)
        if self.op in ("contains", "equals", "startswith", "endswith") and not self.value.strip():
            raise OrganizeError(f"op {self.op!r} needs a non-empty value")

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        return cls(
            field=d["field"],
            op=d["op"],
            value=d.get("value", ""),
            action=d.get("action", "folder"),
            target=d.get("target", ""),
            reason=d.get("reason", ""),
        )

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "op": self.op,
            "value": self.value,
            "action": self.action,
            "target": self.target,
            "reason": self.reason,
        }


@dataclass
class OrganizeSpec:
    """An ordered rule-set plus a fallback for unmatched files. ``summary`` is
    Claude's one-line paraphrase of how it read the instruction (shown back to the
    user so they can sanity-check the interpretation before applying)."""

    rules: list[Rule]
    default: str = "leave"
    summary: str = ""

    def __post_init__(self) -> None:
        if self.default not in DEFAULTS:
            raise OrganizeError(f"unknown default {self.default!r}; valid: {', '.join(DEFAULTS)}")

    @classmethod
    def from_dict(cls, d: dict) -> "OrganizeSpec":
        return cls(
            rules=[Rule.from_dict(r) for r in d.get("rules", [])],
            default=d.get("default", "leave"),
            summary=d.get("summary", ""),
        )

    def to_dict(self) -> dict:
        return {
            "rules": [r.to_dict() for r in self.rules],
            "default": self.default,
            "summary": self.summary,
        }


# --- matching -------------------------------------------------------------


def _field_value(meta: TrackMeta, field: str) -> str | None:
    if field == "artist":
        return meta.artist
    if field == "title":
        return meta.title
    if field == "genre":
        return meta.genre
    if field == "filename":
        return meta.path.name
    if field == "extension":
        return meta.path.suffix.lower()
    return None  # "quality" is decided structurally, not by string match


def matches(rule: Rule, meta: TrackMeta) -> bool:
    """True if ``rule`` applies to ``meta``. Unknown/missing fields never match
    (we never guess), and low-quality is read structurally, never from text."""
    if rule.op == "any":
        return True
    # Low-quality is triggered by the op alone, so the report (which keys off the
    # op) always describes the match accurately. A `quality` field paired with a
    # text op falls through to the string branch and simply matches nothing.
    if rule.op == "is_low_quality":
        return meta.low_bitrate
    hay = _field_value(meta, rule.field)
    needle = (rule.value or "").casefold()
    if hay is None or not needle:
        return False
    hay = hay.casefold()
    if rule.op == "contains":
        return needle in hay
    if rule.op == "equals":
        return hay == needle
    if rule.op == "startswith":
        return hay.startswith(needle)
    if rule.op == "endswith":
        return hay.endswith(needle)
    return False


# --- plan building --------------------------------------------------------


def _dest_for(
    root: Path, path: Path, meta: TrackMeta, action: str, target: str, reserved: set[str]
) -> tuple[Path | None, str]:
    """The (destination, action-kind) for one file under a resolved action.

    Returns ``(None, "")`` for 'leave it where it is'. ``folder`` and ``by_genre``
    keep the filename and only relocate; ``rename_artist_title`` renames in place;
    ``quarantine`` sets the file aside (never deletes). Destinations always sit
    inside ``root`` and go through ``collision_free`` so nothing is clobbered.
    """
    suffix = path.suffix.lower()
    if action == "quarantine":
        return quarantine_dest(root, path.name, reserved), QUARANTINE
    if action == "folder":
        folder = root / sanitize_component(target or "Unsorted")
        return collision_free(folder / (path.stem + suffix), reserved, ignore=path), MOVE
    if action == "by_genre":
        if not meta.genre:
            return None, ""  # never invent a genre folder
        folder = root / sanitize_component(meta.genre)
        return collision_free(folder / (path.stem + suffix), reserved, ignore=path), MOVE
    if action == "rename_artist_title":
        dest = path.parent / (target_stem(meta) + suffix)
        return collision_free(dest, reserved, ignore=path), MOVE
    return None, ""


def _resolve(rule: Rule | None, default: str) -> tuple[str, str]:
    """Map a matched rule (or the spec default) to an (action, target)."""
    if rule is not None:
        return rule.action, rule.target
    if default == "by_genre":
        return "by_genre", ""
    if default == "rename_artist_title":
        return "rename_artist_title", ""
    return "leave", ""


def _reason(rule: Rule | None, action: str, meta: TrackMeta, dest: Path) -> str:
    if rule is not None and rule.reason:
        return rule.reason
    if action == "quarantine":
        return "set aside"
    if action == "rename_artist_title":
        return "rename to Artist - Title" if meta.has_artist_title else "clean filename"
    return f"file into {dest.parent.name}/"


def build_organize_plan(
    library_root: Path,
    spec: OrganizeSpec,
    rekordbox_xml: Path | None = None,
) -> tuple[Plan, str]:
    """Apply ``spec`` to ``library_root`` and return ``(plan, report_md)``.

    Pure and deterministic: it reads metadata and computes moves, but changes
    nothing on disk. The returned Plan is the same kind ``apply``/``undo`` handle.
    """
    root = library_root.absolute()
    files = audio_files(root)
    metas = {p: read_meta(p) for p in files}

    actions: list[Action] = []
    reserved: set[str] = set()
    hits = [0] * len(spec.rules)  # how many files each rule matched
    default_hits = 0
    left = 0

    for p in files:
        meta = metas[p]
        matched = next((i for i, r in enumerate(spec.rules) if matches(r, meta)), None)
        rule = spec.rules[matched] if matched is not None else None
        action, target = _resolve(rule, spec.default)
        if action == "leave":
            left += 1
            continue
        dest, kind = _dest_for(root, p, meta, action, target, reserved)
        if dest is None or dest == p:
            # Matched (or hit the default) but nothing to do — e.g. by_genre on an
            # untagged file. Count it as left, not as a move the rule produced.
            left += 1
            continue
        reserved.add(norm_key(dest))
        actions.append(Action(kind, p, dest, _reason(rule, action, meta, dest)))
        # Tally only files an action was actually produced for, so the report's
        # per-rule counts match the plan.
        if matched is None:
            default_hits += 1
        else:
            hits[matched] += 1

    # Never leave rekordbox pointing at a dead path: every moved/quarantined file's
    # Location follows it (same policy as the other planners — a quarantined file
    # still resolves; nothing is deleted).
    redirects = {a.src: a.dest for a in actions}
    plan = Plan(
        library_root=root,
        actions=actions,
        rekordbox_xml=rekordbox_xml,
        location_redirects=redirects,
    )
    report_md = _render_report(spec, hits, default_hits, left, len(actions))
    return plan, report_md


def _render_report(
    spec: OrganizeSpec, hits: list[int], default_hits: int, left: int, n_actions: int
) -> str:
    lines = ["# Organize plan", ""]
    if spec.summary:
        lines += [f"**Interpreted as:** {spec.summary}", ""]
    lines += [f"- {n_actions} change(s) proposed, {left} file(s) left in place", "", "## Rules"]
    if not spec.rules:
        lines.append("- (no rules — only the default applied)")
    for r, n in zip(spec.rules, hits):
        cond = "any file" if r.op == "any" else (
            "low-quality files" if r.op == "is_low_quality" else f"{r.field} {r.op} {r.value!r}"
        )
        act = f"→ {r.action}" + (f" {r.target}/" if r.action == "folder" and r.target else "")
        lines.append(f"- `{cond}` {act}  — applied to {n} file(s)")
    lines += ["", f"**Default** for unmatched: `{spec.default}` (applied to {default_hits} file(s))", ""]
    return "\n".join(lines)
