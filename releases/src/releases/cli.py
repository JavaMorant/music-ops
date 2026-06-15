"""releases CLI — scan, list, plan, status, dashboard.

    releases scan                         # index ~/ProducerLibrary/projects (read-only)
    releases list --closest               # what's nearest done
    releases plan --cadence single/3w --target "EP by Aug 31"
    releases status "Encara" scheduled    # move a project along the pipeline
    releases dashboard                    # counts, schedule, overdue

The scan only ever READS the library. All state lives in a separate db
(default ~/.releases/releases.db).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Optional

import typer

from . import db as dbmod
from . import release as relmod
from .model import SETTABLE_STAGES, stage_label
from .plan import PlanError, Slot, plan_release, plan_releases
from .release import VALID_KINDS, ReleaseError
from .scan import scan as scan_library
from .score import rank, score_project

app = typer.Typer(
    help="Release tracker: turn idle ProducerLibrary projects into a shipping schedule.",
    no_args_is_help=True,
)

DEFAULT_ROOT = Path.home() / "ProducerLibrary" / "projects"
# The music library is read-only; the index must never live inside it. Module
# global so tests can point it at a fixture. Covers ~/ProducerLibrary entirely.
PROTECTED_LIBRARY = Path.home() / "ProducerLibrary"

DbOpt = Annotated[Path, typer.Option("--db", help="SQLite index location (kept outside the library)")]


def _reject_db_in_library(db_path: Path) -> None:
    """Refuse a --db that resolves inside the music library — writing the index
    there would violate the non-negotiable read-only invariant (applies to EVERY
    command, not just scan)."""
    db_r = db_path.resolve()
    lib = PROTECTED_LIBRARY.resolve()
    if db_r == lib or lib in db_r.parents:
        typer.secho(
            f"Refusing --db {db_path}: it is inside the music library {lib}. "
            "Keep the index outside it (default ~/.releases/releases.db).",
            fg="red", err=True,
        )
        raise typer.Exit(1)


def _open(db_path: Path):
    _reject_db_in_library(db_path)
    return dbmod.connect(db_path)


def _fmt_meta(p) -> str:
    bits = []
    if p.bpm:
        bits.append(f"{p.bpm}bpm")
    if p.key:
        bits.append(p.key)
    if p.genre and p.genre != "unknown":
        bits.append(p.genre)
    if p.has_bounce:
        bits.append("bounce")
    return "  ".join(bits)


@app.command()
def scan(
    root: Annotated[Path, typer.Argument(help="Library projects root to scan (read-only)")] = DEFAULT_ROOT,
    db: DbOpt = dbmod.DEFAULT_DB,
) -> None:
    """Scan the projects library into the index. READ-ONLY — nothing in the
    library is moved, renamed, or written."""
    if not root.exists():
        typer.secho(f"Scan root not found: {root}", fg="red", err=True)
        raise typer.Exit(1)
    # Non-negotiable: never write the index *into* the library we're scanning.
    root_r = root.resolve()
    db_r = db.resolve()
    if db_r == root_r or root_r in db_r.parents:
        typer.secho(
            f"Refusing --db {db}: it is inside the scan root {root}. "
            "Keep the index outside the library (e.g. ~/.releases/releases.db).",
            fg="red", err=True,
        )
        raise typer.Exit(1)
    typer.echo(f"Scanning {root}  (read-only)…")
    projects = scan_library(root)
    conn = _open(db)
    n = dbmod.upsert_projects(conn, projects)
    flps = sum(1 for p in projects if p.kind == "project")
    bounces = sum(1 for p in projects if p.kind == "bounce")
    typer.secho(f"Indexed {n} projects  ({flps} with .flp, {bounces} standalone bounces)", fg="green")
    typer.echo(f"Index: {db}")


@app.command(name="list")
def list_cmd(
    stage: Annotated[Optional[str], typer.Option("--stage", help="Filter to one stage key")] = None,
    closest: Annotated[bool, typer.Option("--closest", help="Rank by closeness-to-done")] = False,
    limit: Annotated[int, typer.Option("--limit", help="Max rows (0 = all)")] = 0,
    db: DbOpt = dbmod.DEFAULT_DB,
) -> None:
    """List indexed projects. ``--closest`` ranks the ones nearest shippable."""
    conn = _open(db)
    projects = dbmod.all_projects(conn)
    if not projects:
        typer.echo("No projects indexed yet — run:  releases scan")
        return
    if stage:
        projects = [p for p in projects if p.effective_stage == stage]

    scored = rank(projects)
    if not closest:
        scored.sort(key=lambda s: (s.project.last_modified == 0, -s.project.last_modified))
    if limit > 0:
        scored = scored[:limit]

    header = "score  stage            project"
    if closest:
        typer.echo(header)
        typer.echo("-" * len(header))
    for s in scored:
        p = s.project
        score = f"{s.score:5.1f}" if closest else "     "
        line = f"{score}  {stage_label(p.effective_stage)[:15]:<15}  {p.name}"
        typer.echo(line)
        meta = _fmt_meta(p)
        if meta:
            typer.echo(f"                        {meta}")
    typer.echo(f"\n{len(scored)} project(s).")


def _render_slots(slots: list[Slot]) -> None:
    for s in slots:
        if s.slot_type == "ep":
            typer.secho(f"  {s.slot_date.isoformat()}  ★ {s.label}", fg="magenta")
        else:
            p = s.scored.project
            meta = _fmt_meta(p)
            tail = f"   [{meta}]" if meta else ""
            typer.echo(f"  {s.slot_date.isoformat()}  single  {p.name}{tail}")


def _schedule_entries(slots: list[Slot]) -> list[dict]:
    out = []
    for s in slots:
        if s.slot_type == "ep":
            path = f"release:{s.release_id}" if s.release_id else f"EP::{s.label}"
            out.append({"path": path, "name": s.label, "slot_date": s.slot_date.isoformat(),
                        "slot_type": "ep", "release_id": s.release_id, "position": None})
        else:
            p = s.scored.project
            out.append({"path": p.path, "name": p.name, "slot_date": s.slot_date.isoformat(),
                        "slot_type": "single", "release_id": s.release_id, "position": s.position})
    return out


@app.command()
def plan(
    cadence: Annotated[str, typer.Option("--cadence", help='e.g. "single/3w"')] = "single/3w",
    target: Annotated[Optional[str], typer.Option("--target", help='e.g. "EP by Aug 31"')] = None,
    count: Annotated[Optional[int], typer.Option("--count", help="Singles to schedule when no target deadline")] = None,
    release: Annotated[Optional[str], typer.Option("--release", help="Schedule a curated release (id or name) instead of auto-picking")] = None,
    together: Annotated[bool, typer.Option("--together", help="EP only: drop all tracks on the EP date (no lead singles)")] = False,
    save: Annotated[bool, typer.Option("--save/--no-save", help="Persist the calendar for the dashboard")] = True,
    db: DbOpt = dbmod.DEFAULT_DB,
) -> None:
    """Generate a release calendar — from the closest-to-done projects, or from a
    hand-curated release with ``--release``."""
    conn = _open(db)
    projects = dbmod.all_projects(conn)
    if not projects:
        typer.echo("No projects indexed yet — run:  releases scan")
        return

    rel = None
    n_missing = 0
    n_present = 0
    try:
        if release:
            rel = relmod.resolve_release(conn, release)
            tracks = dbmod.get_release_tracks(conn, rel["id"])
            present = [t["project"] for t in tracks if not t["missing"]]
            n_missing = sum(1 for t in tracks if t["missing"])
            n_present = len(present)
            if not present:
                typer.secho(f"Release {rel['name']!r} has no schedulable tracks.", fg="red", err=True)
                raise typer.Exit(1)
            slots, cad, tgt = plan_release(
                present, cadence, target, date.today(),
                rel["kind"], rel["id"], rel["name"], together=together,
            )
        else:
            slots, cad, tgt = plan_releases(projects, cadence, target, date.today(), count=count)
    except (PlanError, ReleaseError) as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(1)

    if rel:
        typer.echo(f"\nRelease calendar — {rel['name']} ({rel['kind']})")
        if n_missing:
            typer.secho(f"  ({n_missing} track(s) missing from last scan — skipped)", fg="yellow")
        n_singles = sum(1 for s in slots if s.slot_type == "single")
        if not together and rel["kind"] == "ep" and n_singles < n_present:
            typer.secho(
                f"  ({n_present - n_singles} track(s) won't fit as lead singles before the EP "
                "— they still ship on the EP date)", fg="yellow")
    else:
        typer.echo(f"\nRelease calendar — one {cad.kind} every {cad.interval_days} days")
    if tgt.deadline:
        typer.echo(f"Target: {tgt.label} by {tgt.deadline.isoformat()}")
    typer.echo("")
    _render_slots(slots)

    if save:
        entries = _schedule_entries(slots)
        if rel:
            # Scope the wipe to THIS release so other curated releases survive.
            dbmod.replace_release_schedule(conn, rel["id"], entries)
            sched_date = tgt.deadline.isoformat() if tgt.deadline else (
                slots[-1].slot_date.isoformat() if slots else None)
            dbmod.set_release_status(conn, rel["id"], "scheduled", sched_date)
        else:
            dbmod.replace_schedule(conn, entries)
        typer.secho(f"\nSaved {len(entries)} slots to the schedule (releases dashboard).", fg="green")


@app.command()
def status(
    project: Annotated[str, typer.Argument(help="Project name or path substring")],
    stage: Annotated[str, typer.Argument(help=f"New stage: {', '.join(SETTABLE_STAGES)}")],
    note: Annotated[Optional[str], typer.Option("--note", help="Why / what changed")] = None,
    db: DbOpt = dbmod.DEFAULT_DB,
) -> None:
    """Move a project along the pipeline (logged). Writes only to the index —
    never to the library."""
    if stage not in SETTABLE_STAGES:
        typer.secho(f"Unknown stage {stage!r}. Valid: {', '.join(SETTABLE_STAGES)}", fg="red", err=True)
        raise typer.Exit(1)
    conn = _open(db)
    matches = dbmod.find_projects(conn, project)
    if not matches:
        typer.secho(f"No project matches {project!r}.", fg="red", err=True)
        raise typer.Exit(1)
    if len(matches) > 1:
        typer.secho(f"{project!r} is ambiguous — {len(matches)} matches:", fg="yellow", err=True)
        for m in matches[:12]:
            typer.echo(f"  {m.name}  ({m.path})", err=True)
        raise typer.Exit(1)
    p = matches[0]
    old = p.effective_stage
    dbmod.set_stage(conn, p, stage, note)
    typer.secho(f"{p.name}: {stage_label(old)} → {stage_label(stage)}", fg="green")


@app.command()
def dashboard(db: DbOpt = dbmod.DEFAULT_DB) -> None:
    """Counts by stage, what's scheduled, what's overdue."""
    conn = _open(db)
    projects = dbmod.all_projects(conn)
    if not projects:
        typer.echo("No projects indexed yet — run:  releases scan")
        return

    counts = dbmod.counts_by_stage(conn)
    typer.secho("By stage", bold=True)
    # Order by the stage weight so the dashboard reads done→raw.
    from .model import STAGES
    ordered = sorted(counts.items(), key=lambda kv: -STAGES[kv[0]].weight if kv[0] in STAGES else 0)
    for key, n in ordered:
        typer.echo(f"  {stage_label(key):<24} {n}")
    typer.echo(f"  {'TOTAL':<24} {len(projects)}")

    typer.echo("")
    typer.secho("Closest to done", bold=True)
    unshipped = [p for p in projects if p.effective_stage != "released"]
    for s in rank(unshipped)[:5]:
        typer.echo(f"  {s.score:5.1f}  {s.project.name}")

    releases = dbmod.list_releases(conn)
    rel_status = {r["id"]: r["status"] for r in releases}
    if releases:
        typer.echo("")
        typer.secho("Releases", bold=True)
        for r in releases:
            glyph = "•" if r["kind"] == "single" else "◆"
            td = r["target_date"] or "unscheduled"
            typer.echo(f"  {glyph} {r['name']:<26} {r['status']:<9} {r['n_tracks']} tracks  ({td})")

    sched = dbmod.get_schedule(conn)
    typer.echo("")
    typer.secho("Schedule", bold=True)
    if not sched:
        typer.echo("  (nothing scheduled — run: releases plan)")
        return
    today = date.today()
    overdue = []
    for row in sched:
        d = datetime.strptime(row["slot_date"], "%Y-%m-%d").date()
        is_ep = row["slot_type"] == "ep"
        rid = row["release_id"]
        mark = "★" if is_ep else " "
        # A slot whose owning release is already released (or was deleted) is
        # never overdue — single or EP. A curated EP still un-shipped past its
        # date IS overdue; a legacy EP label (no release_id) never is.
        active = rid is None or rel_status.get(rid) in ("planning", "scheduled")
        if not active:
            overdue_now = False
        elif is_ep:
            overdue_now = d < today and rid is not None
        else:
            overdue_now = d < today
        flag = typer.style("  OVERDUE", fg="red") if overdue_now else ""
        if overdue_now:
            overdue.append(row)
        typer.echo(f"  {row['slot_date']} {mark} {row['name']}{flag}")
    if overdue:
        typer.secho(f"\n{len(overdue)} overdue — finish or reschedule.", fg="red")


# --- release sub-app: curate singles/EPs from scanned projects -------------

release_app = typer.Typer(
    no_args_is_help=True,
    help="Curate single/EP releases from scanned projects — index-only, no files moved.",
)
app.add_typer(release_app, name="release")


def _resolve_release_or_exit(conn, query: str):
    try:
        return relmod.resolve_release(conn, query)
    except ReleaseError as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(1)


@release_app.command("new")
def release_new(
    name: Annotated[Optional[str], typer.Argument(help="Release name (omit → auto 'Untitled N')")] = None,
    kind: Annotated[str, typer.Option("--kind", help="single | ep")] = "ep",
    db: DbOpt = dbmod.DEFAULT_DB,
) -> None:
    """Create a release. Move tracks into it with `releases release add`."""
    if kind not in VALID_KINDS:
        typer.secho(f"--kind must be one of {sorted(VALID_KINDS)}", fg="red", err=True)
        raise typer.Exit(1)
    conn = _open(db)
    nm = name or dbmod.next_untitled_name(conn)
    rid = dbmod.create_release(conn, nm, kind)
    typer.secho(f"Created release #{rid}: {nm} ({kind})", fg="green")
    if not name:
        typer.echo(f"  rename it:  releases release rename {rid} \"My EP\"")


@release_app.command("ls")
def release_ls(db: DbOpt = dbmod.DEFAULT_DB) -> None:
    """List all releases."""
    conn = _open(db)
    rels = dbmod.list_releases(conn)
    if not rels:
        typer.echo("No releases yet — run:  releases release new")
        return
    for r in rels:
        glyph = "•" if r["kind"] == "single" else "◆"
        td = r["target_date"] or "—"
        typer.echo(
            f"  #{r['id']:<3} {glyph} {r['name']:<28} {r['kind']:<6} {r['status']:<9} "
            f"{r['n_tracks']} tracks  drops {td}"
        )


@release_app.command("show")
def release_show(
    release: Annotated[str, typer.Argument(help="Release id or name")],
    db: DbOpt = dbmod.DEFAULT_DB,
) -> None:
    """Show a release's ordered tracklist (with readiness + missing flags)."""
    conn = _open(db)
    rel = _resolve_release_or_exit(conn, release)
    td = f"  drops {rel['target_date']}" if rel["target_date"] else ""
    typer.secho(f"\n{rel['name']}  ({rel['kind']}, {rel['status']}){td}", bold=True)
    tracks = dbmod.get_release_tracks(conn, rel["id"])
    if not tracks:
        typer.echo("  (no tracks yet — releases release add <release> <project>)")
        return
    ready = 0
    for t in tracks:
        if t["missing"]:
            typer.secho(f"  {t['position']:>2}. {t['name']}   MISSING (not in last scan)", fg="yellow")
            continue
        p = t["project"]
        sc = score_project(p)
        meta = _fmt_meta(p)
        tail = f"   {meta}" if meta else ""
        if p.effective_stage in ("complete", "released", "track-list"):
            ready += 1
        typer.echo(f"  {t['position']:>2}. {p.name}   [{stage_label(p.effective_stage)}]  {sc.score:.0f}{tail}")
    typer.echo(f"\n  {len(tracks)} track(s), {ready} near-ready.")


@release_app.command("add")
def release_add(
    release: Annotated[str, typer.Argument(help="Release id or name")],
    queries: Annotated[Optional[list[str]], typer.Argument(help="Project name/path substrings")] = None,
    top: Annotated[Optional[int], typer.Option("--top", help="Bulk-add the N closest-to-done not already in a release")] = None,
    at: Annotated[Optional[int], typer.Option("--at", help="Insert at this 1-based position (else append)")] = None,
    db: DbOpt = dbmod.DEFAULT_DB,
) -> None:
    """Add tracks to a release — by query, or `--top N` for the closest-to-done."""
    conn = _open(db)
    rel = _resolve_release_or_exit(conn, release)
    queries = queries or []
    if top is not None and top < 1:
        typer.secho("--top must be a positive integer.", fg="red", err=True)
        raise typer.Exit(1)
    if not queries and top is None:
        typer.secho("Give a project query or --top N.", fg="red", err=True)
        raise typer.Exit(1)
    items, problems = relmod.collect_add_items(conn, queries, top)
    for pb in problems:
        typer.secho(f"  skipped: {pb}", fg="yellow")
    if not items:
        typer.secho("Nothing to add.", fg="red", err=True)
        raise typer.Exit(1 if problems else 0)
    added, skipped = dbmod.add_tracks(conn, rel["id"], items, at=at)
    typer.secho(f"Added {len(added)} track(s) to {rel['name']}.", fg="green")
    if skipped:
        typer.echo(f"  ({len(skipped)} already in the release, skipped)")


@release_app.command("move")
def release_move(
    track: Annotated[str, typer.Argument(help="Track name/path substring (across releases)")],
    to: Annotated[str, typer.Option("--to", help="Destination release id or name")],
    at: Annotated[Optional[int], typer.Option("--at", help="Insert at this 1-based position")] = None,
    db: DbOpt = dbmod.DEFAULT_DB,
) -> None:
    """Move a track from whatever release holds it into another."""
    conn = _open(db)
    try:
        name, frm, dest = relmod.move_track(conn, track, to, at)
    except ReleaseError as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(1)
    typer.secho(f"Moved {name!r}: {frm} → {dest}", fg="green")


@release_app.command("rm")
def release_rm(
    release: Annotated[str, typer.Argument(help="Release id or name")],
    queries: Annotated[list[str], typer.Argument(help="Track name/path substrings to remove")],
    db: DbOpt = dbmod.DEFAULT_DB,
) -> None:
    """Remove track(s) from a release (the project itself is never touched)."""
    conn = _open(db)
    rel = _resolve_release_or_exit(conn, release)
    tracks = dbmod.get_release_tracks(conn, rel["id"])
    removed = 0
    for q in queries:
        ql = q.lower()
        m = [t for t in tracks if ql in t["name"].lower() or ql in t["path"].lower()]
        if not m:
            typer.secho(f"  skipped: no track matches {q!r}", fg="yellow")
            continue
        if len(m) > 1:
            typer.secho(f"  skipped: {q!r} ambiguous within this release", fg="yellow")
            continue
        dbmod.remove_track(conn, rel["id"], m[0]["path"])
        tracks = [t for t in tracks if t["path"] != m[0]["path"]]
        removed += 1
    if removed == 0:
        typer.secho("No matching tracks removed.", fg="red", err=True)
        raise typer.Exit(1)
    typer.secho(f"Removed {removed} track(s) from {rel['name']}.", fg="green")


@release_app.command("reorder")
def release_reorder(
    release: Annotated[str, typer.Argument(help="Release id or name")],
    queries: Annotated[list[str], typer.Argument(help="Track queries, front-to-back")],
    db: DbOpt = dbmod.DEFAULT_DB,
) -> None:
    """Set track order; tracks you don't name keep their order after the listed ones."""
    conn = _open(db)
    rel = _resolve_release_or_exit(conn, release)
    try:
        relmod.reorder(conn, rel["id"], queries)
    except ReleaseError as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(1)
    typer.secho(f"Reordered {rel['name']}.", fg="green")


@release_app.command("rename")
def release_rename(
    release: Annotated[str, typer.Argument(help="Release id or current name")],
    name: Annotated[str, typer.Argument(help="New name")],
    kind: Annotated[Optional[str], typer.Option("--kind", help="Also change kind: single | ep")] = None,
    db: DbOpt = dbmod.DEFAULT_DB,
) -> None:
    """Rename a release (and optionally flip its kind)."""
    if kind is not None and kind not in VALID_KINDS:
        typer.secho(f"--kind must be one of {sorted(VALID_KINDS)}", fg="red", err=True)
        raise typer.Exit(1)
    conn = _open(db)
    rel = _resolve_release_or_exit(conn, release)
    dbmod.rename_release(conn, rel["id"], name=name, kind=kind)
    typer.secho(f"Renamed #{rel['id']} → {name}" + (f" ({kind})" if kind else ""), fg="green")


@release_app.command("delete")
def release_delete(
    release: Annotated[str, typer.Argument(help="Release id or name")],
    force: Annotated[bool, typer.Option("--force", help="Skip confirmation")] = False,
    db: DbOpt = dbmod.DEFAULT_DB,
) -> None:
    """Delete a release container (its tracks unlink; projects/files untouched)."""
    conn = _open(db)
    rel = _resolve_release_or_exit(conn, release)
    if not force and not typer.confirm(
        f"Delete release {rel['name']!r}? (tracks unlink; projects and files are untouched)"
    ):
        typer.echo("Aborted.")
        return
    dbmod.delete_release(conn, rel["id"])
    typer.secho(f"Deleted release {rel['name']!r}.", fg="green")


@release_app.command("ship")
def release_ship(
    release: Annotated[str, typer.Argument(help="Release id or name")],
    db: DbOpt = dbmod.DEFAULT_DB,
) -> None:
    """Mark a release released — cascades its tracks to the 'released' stage (logged)."""
    conn = _open(db)
    rel = _resolve_release_or_exit(conn, release)
    tracks = dbmod.get_release_tracks(conn, rel["id"])
    dbmod.set_release_status(conn, rel["id"], "released")
    n = 0
    for t in tracks:
        if not t["missing"]:
            dbmod.set_stage(conn, t["project"], "released", f"shipped with {rel['name']}")
            n += 1
    typer.secho(f"Shipped {rel['name']} — {n} track(s) marked released.", fg="green")


if __name__ == "__main__":
    app()
