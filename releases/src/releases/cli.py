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
from .model import SETTABLE_STAGES, stage_label
from .plan import PlanError, plan_releases
from .scan import scan as scan_library
from .score import rank

app = typer.Typer(
    help="Release tracker: turn idle ProducerLibrary projects into a shipping schedule.",
    no_args_is_help=True,
)

DEFAULT_ROOT = Path.home() / "ProducerLibrary" / "projects"

DbOpt = Annotated[Path, typer.Option("--db", help="SQLite index location (kept outside the library)")]


def _open(db_path: Path):
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


@app.command()
def plan(
    cadence: Annotated[str, typer.Option("--cadence", help='e.g. "single/3w"')] = "single/3w",
    target: Annotated[Optional[str], typer.Option("--target", help='e.g. "EP by Aug 31"')] = None,
    count: Annotated[Optional[int], typer.Option("--count", help="Singles to schedule when no target deadline")] = None,
    save: Annotated[bool, typer.Option("--save/--no-save", help="Persist the calendar for the dashboard")] = True,
    db: DbOpt = dbmod.DEFAULT_DB,
) -> None:
    """Generate a release calendar from the closest-to-done projects."""
    conn = _open(db)
    projects = dbmod.all_projects(conn)
    if not projects:
        typer.echo("No projects indexed yet — run:  releases scan")
        return
    try:
        slots, cad, tgt = plan_releases(projects, cadence, target, date.today(), count=count)
    except PlanError as e:
        typer.secho(str(e), fg="red", err=True)
        raise typer.Exit(1)

    typer.echo(f"\nRelease calendar — one {cad.kind} every {cad.interval_days} days")
    if tgt.deadline:
        typer.echo(f"Target: {tgt.label} by {tgt.deadline.isoformat()}")
    typer.echo("")
    for s in slots:
        if s.slot_type == "ep":
            typer.secho(f"  {s.slot_date.isoformat()}  ★ {s.label}", fg="magenta")
        else:
            p = s.scored.project
            meta = _fmt_meta(p)
            tail = f"   [{meta}]" if meta else ""
            typer.echo(f"  {s.slot_date.isoformat()}  {cad.kind:<7} {p.name}{tail}")

    if save:
        entries = []
        for s in slots:
            if s.slot_type == "ep":
                entries.append((f"EP::{s.label}", s.label, s.slot_date.isoformat(), "ep"))
            else:
                entries.append((s.scored.project.path, s.scored.project.name, s.slot_date.isoformat(), "single"))
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
        mark = "★" if row["slot_type"] == "ep" else " "
        flag = ""
        if d < today and row["slot_type"] != "ep":
            flag = typer.style("  OVERDUE", fg="red")
            overdue.append(row)
        typer.echo(f"  {row['slot_date']} {mark} {row['name']}{flag}")
    if overdue:
        typer.secho(f"\n{len(overdue)} overdue — finish or reschedule.", fg="red")


if __name__ == "__main__":
    app()
