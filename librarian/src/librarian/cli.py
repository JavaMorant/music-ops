"""librarian CLI — plan / apply / undo over the music library.

The whole flow is reversible and dry-run-first:

    librarian plan  <library-root>      # review a plan (no changes)
    librarian apply <plan.json>         # execute a reviewed plan (journaled)
    librarian undo  <run-id>            # reverse a run completely
    librarian runs                      # list applied runs
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer

from . import ai
from .cleanup import build_cleanup_plan
from .engine import EngineError, apply_plan, undo_run
from .inbox import InboxError, build_inbox_plan
from .journal import APPLIED, DONE, list_runs
from .metadata import read_meta
from .model import Plan
from .organize import OrganizeError, OrganizeSpec, build_organize_plan
from .paths import audio_files
from .planner import build_plan
from .retag import TagProposal, build_retag_plan
from .tags import WRITABLE_EXTS

app = typer.Typer(
    help="Reversible DJ-library organiser: plan, apply, undo. Dry-run by default.",
    no_args_is_help=True,
)

DEFAULT_RUNS_DIR = Path(".librarian/runs")


def _rel(path: Path, root: Path) -> str:
    """Display ``path`` relative to ``root`` when possible, else absolute."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _print_plan(plan: Plan) -> None:
    if not plan.actions and not plan.tag_edits:
        typer.echo("No changes proposed — the library is already clean.")
        return
    root = plan.library_root
    if plan.actions:
        typer.echo(f"\nPlan for {root}  ({len(plan.actions)} actions)")
        if plan.rekordbox_xml:
            typer.echo(f"rekordbox XML to rewrite: {plan.rekordbox_xml}")
        typer.echo("")
        for i, a in enumerate(plan.actions, 1):
            tag = typer.style(f"[{a.kind}]", fg="yellow" if a.kind == "quarantine" else "cyan")
            typer.echo(f"{i:>3} {tag} {_rel(a.src, root)}")
            typer.echo(f"      -> {_rel(a.dest, root)}")
            typer.echo(f"      reason: {a.reason}")
    if plan.tag_edits:
        typer.echo(f"\nTag repairs for {root}  ({len(plan.tag_edits)} files)\n")
        for i, t in enumerate(plan.tag_edits, 1):
            tag = typer.style("[retag]", fg="magenta")
            typer.echo(f"{i:>3} {tag} {_rel(t.path, root)}")
            for field, value in t.fields.items():
                typer.echo(f"      {field} → {value!r}")
            typer.echo(f"      reason: {t.reason}")


@app.command()
def plan(
    library_root: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="Library root to scan")],
    rekordbox_xml: Annotated[Optional[Path], typer.Option("--rekordbox-xml", exists=True, dir_okay=False, help="rekordbox collection XML to keep in sync")] = None,
    out: Annotated[Path, typer.Option("--out", help="Where to write the reviewable plan JSON")] = Path("plan.json"),
) -> None:
    """Scan the library and write a reviewable plan. Makes NO changes."""
    p = build_plan(library_root.absolute(), rekordbox_xml.absolute() if rekordbox_xml else None)
    _print_plan(p)
    out.write_text(json.dumps(p.to_dict(), indent=2), encoding="utf-8")
    typer.echo(f"\nDry run — nothing changed. Plan written to {out}")
    if p.actions:
        typer.echo(f"Review it, then:  librarian apply {out}")


@app.command()
def cleanup(
    library_root: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="Library root to scan")],
    rekordbox_xml: Annotated[Optional[Path], typer.Option("--rekordbox-xml", exists=True, dir_okay=False, help="rekordbox collection XML to keep in sync")] = None,
    organize: Annotated[bool, typer.Option("--organize/--no-organize", help="File tracks into Genre/ folders")] = True,
    out: Annotated[Path, typer.Option("--out", help="Where to write the reviewable plan JSON")] = Path("plan.json"),
    report_out: Annotated[Path, typer.Option("--report", help="Where to write the cleanup report")] = Path("cleanup-report.md"),
) -> None:
    """Deep-clean scan: dedupe, rename to Artist - Title, refile by genre. NO changes."""
    p, report = build_cleanup_plan(
        library_root.absolute(),
        organize_by_genre=organize,
        rekordbox_xml=rekordbox_xml.absolute() if rekordbox_xml else None,
    )
    _print_plan(p)
    out.write_text(json.dumps(p.to_dict(), indent=2), encoding="utf-8")
    report_out.write_text(report.render(), encoding="utf-8")
    typer.echo(f"\nDry run — nothing changed. Plan: {out}   Report: {report_out}")
    if p.actions:
        typer.echo(f"Review both, then:  librarian apply {out}")


@app.command()
def organise(
    library_root: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="Library root (or USB) to organise")],
    apply: Annotated[bool, typer.Option("--apply", help="Apply the plans (gated; default dry-run holds everything)")] = False,
    acoustid_key: Annotated[Optional[str], typer.Option("--acoustid-key", help="AcoustID key (else env ACOUSTID_API_KEY or ~/DJ/.acoustid-key); improves identity on junk filenames")] = None,
    classify: Annotated[bool, typer.Option("--classify/--no-classify", help="AI-classify into genres (default: on when a key is present; force on for clean-tagged libraries)")] = False,
    out_dir: Annotated[Path, typer.Option("--out", help="Where to write plans + report")] = Path("organise-run"),
    rekordbox_xml: Annotated[Optional[Path], typer.Option("--rekordbox-xml", exists=True, dir_okay=False, help="rekordbox XML to keep in sync")] = None,
) -> None:
    """Organise a library/USB: identify → dedup v2 → classify → reviewable plans.

    Dry-run by default (holds everything). With an AcoustID key it fingerprints,
    identifies the real track, and classifies into the 24 buckets; without one it
    dedups and does a deterministic folder migration, flagging the rest for the AI
    pass. `--apply` executes the reviewed dedup + reorg plans via the undo journal.
    """
    from . import identity as idmod
    from .engine import apply_plan
    from .organise import (build_dedup_plan, build_reorg_plan, build_usb_playlists,
                           read_usb_ratings)
    from .organise import organise as run_organise

    root = library_root.absolute()
    rbx = rekordbox_xml.absolute() if rekordbox_xml else None
    key = acoustid_key or idmod.get_api_key()
    run_ai = classify or bool(key)
    is_usb = (root / "PIONEER" / "rekordbox" / "export.pdb").exists()
    typer.echo(f"Organising {root} — {'guest USB' if is_usb else 'library'} · "
               f"identity: {'AcoustID' if key else 'tags/filename (no key)'} · "
               f"classify: {'on' if run_ai else 'off'}")
    res = run_organise(root, key=key, run_ai=run_ai)
    out_dir.mkdir(parents=True, exist_ok=True)

    if is_usb:
        # Guest USB: write importable .m3u8 per bucket; never touch the stick's pdb.
        ratings = read_usb_ratings(root)
        summ = build_usb_playlists(res, out_dir, ratings=ratings)
        d = res.dedup
        typer.echo(f"  files {res.total} → keepers {d['unique_keepers']} · "
                   f"dupes {d['drops']} (versions kept {d['distinct_versions_kept']})")
        typer.echo(f"  {summ['playlists']} importable playlists → {out_dir}")
        typer.echo("Import the .m3u8 into rekordbox → assign to a fresh stick → export. "
                   "Nothing on this stick was modified.")
        return

    dplan = build_dedup_plan(root, res.dup_groups, rekordbox_xml=rbx)
    rplan = build_reorg_plan(res, rekordbox_xml=rbx)
    (out_dir / "dedup-plan.json").write_text(json.dumps(dplan.to_dict(), indent=2), encoding="utf-8")
    (out_dir / "reorg-plan.json").write_text(json.dumps(rplan.to_dict(), indent=2), encoding="utf-8")
    d = res.dedup
    typer.echo(f"  files {res.total} → keepers {d['unique_keepers']} · drops {d['drops']} "
               f"(versions kept {d['distinct_versions_kept']})")
    typer.echo(f"  reorg relocations {res.reorg_actions}"
               + ("" if res.used_ai else f" · needs AI pass {res.needs_ai}"))
    typer.echo(f"  plans → {out_dir}/dedup-plan.json, {out_dir}/reorg-plan.json")

    if apply:
        runs_dir = root / ".librarian" / "runs"
        for plan in (dplan, rplan):
            if plan.actions:
                j = apply_plan(plan, runs_dir.absolute(), backup=True)
                typer.echo(f"  applied {len([a for a in j.actions if a.status == DONE])} ({j.run_id})")
    else:
        typer.echo("\nDry run — nothing moved. Review the plans, then re-run with --apply.")


@app.command()
def organize(
    library_root: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="Library root to scan")],
    instruction: Annotated[Optional[str], typer.Argument(help="Plain-English request, e.g. 'put all Avicii in Festival/ and quarantine the _spotdown rips'")] = None,
    spec_file: Annotated[Optional[Path], typer.Option("--spec", exists=True, dir_okay=False, help="Replay a saved rule-set JSON instead of calling the AI (no API key needed)")] = None,
    rekordbox_xml: Annotated[Optional[Path], typer.Option("--rekordbox-xml", exists=True, dir_okay=False, help="rekordbox collection XML to keep in sync")] = None,
    out: Annotated[Path, typer.Option("--out", help="Where to write the reviewable plan JSON")] = Path("plan.json"),
    report_out: Annotated[Path, typer.Option("--report", help="Where to write the organize report")] = Path("organize-report.md"),
    save_spec: Annotated[Optional[Path], typer.Option("--save-spec", help="Also save the inferred rule-set here (replay later with --spec)")] = None,
) -> None:
    """Organize the library from a plain-English instruction (AI). Dry-run.

    Claude turns your request into a small rule-set; the rules are applied
    deterministically and shown as a reviewable plan — nothing changes until you
    `librarian apply`. The AI only authors rules; it never touches files. Use
    --spec to replay a saved rule-set with no API call. (`cleanup` is the
    non-AI deep-clean.)
    """
    root = library_root.absolute()
    if spec_file:
        try:
            spec = OrganizeSpec.from_dict(json.loads(spec_file.read_text(encoding="utf-8")))
        except (OrganizeError, ValueError) as exc:
            typer.secho(f"Bad spec file: {exc}", fg="red", err=True)
            raise typer.Exit(1)
    else:
        if not instruction:
            typer.secho("Give an instruction, or replay one with --spec.", fg="red", err=True)
            raise typer.Exit(1)
        if not ai.is_available():
            typer.secho(
                "AI organize needs the Anthropic API: set ANTHROPIC_API_KEY and "
                "install the extra (pip install -e '.[ai]').\nThe non-AI equivalent "
                "is:  librarian cleanup",
                fg="red", err=True,
            )
            raise typer.Exit(1)
        files = audio_files(root)
        genres = sorted({m.genre for m in (read_meta(p) for p in files) if m.genre})
        try:
            spec = ai.infer_spec(instruction, sample_names=[p.name for p in files[:80]], genres=genres)
        except ai.AIError as exc:
            typer.secho(f"AI request failed: {exc}", fg="red", err=True)
            raise typer.Exit(1)

    try:
        plan, report_md = build_organize_plan(
            root, spec, rekordbox_xml.absolute() if rekordbox_xml else None
        )
    except OrganizeError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1)

    if spec.summary:
        typer.secho(f"Interpreted as: {spec.summary}", fg="cyan")
    _print_plan(plan)
    out.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    report_out.write_text(report_md, encoding="utf-8")
    if save_spec:
        save_spec.write_text(json.dumps(spec.to_dict(), indent=2), encoding="utf-8")
        typer.echo(f"Saved rule-set: {save_spec}  (replay with --spec)")
    typer.echo(f"\nDry run — nothing changed. Plan: {out}   Report: {report_out}")
    if plan.actions:
        typer.echo(f"Review it, then:  librarian apply {out}")


def _propose_in_batches(candidates, metas, instruction, batch_size):
    """Run ai.propose_tags over ``candidates`` in chunks (one AI call each), mapping
    each chunk's per-index suggestions back to the right file. Resilient: a failed
    batch is skipped with a warning so a long run never loses prior work."""
    batch_size = max(1, min(batch_size, 85))  # >85 risks truncating the JSON reply
    proposals: list[TagProposal] = []
    total = -(-len(candidates) // batch_size)
    for b, start in enumerate(range(0, len(candidates), batch_size), 1):
        chunk = candidates[start:start + batch_size]
        tracks = [
            {"index": i, "filename": p.name, "artist": metas[p].artist,
             "title": metas[p].title, "genre": metas[p].genre}
            for i, p in enumerate(chunk)
        ]
        typer.echo(f"  AI batch {b}/{total} ({len(chunk)} files)…")
        try:
            suggestions = ai.propose_tags(tracks, instruction=instruction)
        except ai.AIError as exc:
            typer.secho(f"    batch {b} failed: {exc} — skipped", fg="yellow", err=True)
            continue
        for s in suggestions:
            if 0 <= s.index < len(chunk) and s.fields:
                proposals.append(TagProposal(
                    path=chunk[s.index], fields=s.fields,
                    confidence=s.confidence, reason=s.note))
    return proposals


@app.command()
def retag(
    library_root: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="Library root to scan")],
    instruction: Annotated[Optional[str], typer.Argument(help="Optional guidance, e.g. 'set genre to Amapiano for the SA artists'")] = None,
    spec_file: Annotated[Optional[Path], typer.Option("--spec", exists=True, dir_okay=False, help="Replay saved proposals JSON instead of calling the AI (no API key needed)")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max files to process, untagged-first (0 = all that need tags)")] = 0,
    batch_size: Annotated[int, typer.Option("--batch-size", help="Files per AI call (max 85)")] = 60,
    all_files: Annotated[bool, typer.Option("--all-files", help="Also re-tag files that already have artist+title (normalise everything)")] = False,
    rekordbox_xml: Annotated[Optional[Path], typer.Option("--rekordbox-xml", exists=True, dir_okay=False, help="rekordbox XML to keep in sync (retag doesn't move files, so paths are unaffected)")] = None,
    out: Annotated[Path, typer.Option("--out", help="Where to write the reviewable plan JSON")] = Path("plan.json"),
    report_out: Annotated[Path, typer.Option("--report", help="Where to write the tag-repair report")] = Path("retag-report.md"),
    save_spec: Annotated[Optional[Path], typer.Option("--save-spec", help="Save the proposals here (replay later with --spec)")] = None,
) -> None:
    """Repair messy/missing tags from filenames (AI). Dry-run.

    Claude proposes clean artist/title/genre from each file's name + current tags;
    the changes are shown as a reviewable plan and only written on `librarian
    apply` (and fully reversible with `undo`). The AI never touches files, and
    musical key/BPM are never written. Use --spec to replay saved proposals with
    no API call.
    """
    root = library_root.absolute()
    if spec_file:
        if instruction:
            typer.secho("--spec replays saved proposals; the instruction is ignored.", fg="yellow")
        try:
            raw = json.loads(spec_file.read_text(encoding="utf-8"))
            proposals = [TagProposal.from_dict(d) for d in raw]
        except (ValueError, KeyError, TypeError) as exc:
            typer.secho(f"Bad spec file: {exc}", fg="red", err=True)
            raise typer.Exit(1)
    else:
        if not ai.is_available():
            typer.secho(
                "AI retag needs the Anthropic API: set ANTHROPIC_API_KEY and "
                "install the extra (pip install -e '.[ai]').\nOr feed proposals "
                "with --spec for a no-API repair.",
                fg="red", err=True,
            )
            raise typer.Exit(1)
        files = audio_files(root)
        if not files:
            typer.echo("No audio files found.")
            raise typer.Exit(0)
        typer.echo(f"Scanning {len(files)} files…")
        metas = {p: read_meta(p) for p in files}
        # Default: only files actually missing artist/title (the real mess, and the
        # cheap high-value pass). --all-files also normalises already-tagged ones.
        if all_files:
            candidates = sorted(files, key=lambda p: p.name.lower())
        else:
            candidates = sorted(
                (p for p in files if not metas[p].has_artist_title),
                key=lambda p: p.name.lower(),
            )
        # Skip formats we can't tag-write (WAV/AIFF) before spending AI on them.
        taggable = [p for p in candidates if p.suffix.lower() in WRITABLE_EXTS]
        if len(taggable) < len(candidates):
            typer.secho(
                f"Skipping {len(candidates) - len(taggable)} file(s) whose format "
                f"can't be tag-written (e.g. WAV).", fg="yellow",
            )
        candidates = taggable
        if limit > 0:
            candidates = candidates[:limit]
        if not candidates:
            typer.echo("Nothing to retag — every file already has artist + title "
                       "(use --all-files to normalise everything).")
            raise typer.Exit(0)
        typer.echo(f"Proposing tags for {len(candidates)} file(s) "
                   f"in batches of {min(batch_size, 85)}…")
        proposals = _propose_in_batches(candidates, metas, instruction, batch_size)

    plan, report_md = build_retag_plan(
        root, proposals, rekordbox_xml.absolute() if rekordbox_xml else None
    )
    _print_plan(plan)
    out.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    report_out.write_text(report_md, encoding="utf-8")
    if save_spec:
        save_spec.write_text(
            json.dumps(
                [{"path": str(p.path), "fields": p.fields, "reason": p.reason,
                  "confidence": p.confidence} for p in proposals],
                indent=2,
            ),
            encoding="utf-8",
        )
        typer.echo(f"Saved proposals: {save_spec}  (replay with --spec)")
    typer.echo(f"\nDry run — nothing changed. Plan: {out}   Report: {report_out}")
    if plan.tag_edits:
        typer.echo(f"Review it, then:  librarian apply {out}")


@app.command()
def inbox(
    library_root: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="Library root — the inbox must live inside it")],
    inbox_dir: Annotated[Optional[Path], typer.Option("--inbox", file_okay=False, help="Folder to drain (default: <library-root>/Inbox)")] = None,
    rekordbox_xml: Annotated[Optional[Path], typer.Option("--rekordbox-xml", exists=True, dir_okay=False, help="Collection XML: new tracks are ADDED to it + a playlist")] = None,
    playlist: Annotated[str, typer.Option("--playlist", help="Playlist node to create/append in the XML")] = "New This Week",
    organize: Annotated[bool, typer.Option("--organize/--no-organize", help="File new tracks into Genre/ folders")] = True,
    out: Annotated[Path, typer.Option("--out", help="Where to write the reviewable plan JSON")] = Path("plan.json"),
    report_out: Annotated[Path, typer.Option("--report", help="Where to write the inbox report")] = Path("inbox-report.md"),
) -> None:
    """Drain the Inbox: dedupe against the library, file new tracks, add them to
    rekordbox + a 'New This Week' playlist. Dry-run by default — NO changes."""
    root = library_root.absolute()
    inbox_path = (inbox_dir or (root / "Inbox")).absolute()
    if not inbox_path.is_dir():
        typer.echo(f"Inbox {inbox_path} does not exist — nothing to file.")
        raise typer.Exit(0)
    try:
        p, report = build_inbox_plan(
            root,
            inbox_path,
            organize_by_genre=organize,
            rekordbox_xml=rekordbox_xml.absolute() if rekordbox_xml else None,
            playlist_name=playlist,
        )
    except InboxError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1)
    if not p.actions:
        typer.echo("Inbox empty — nothing to file.")
        report_out.write_text(report.render(), encoding="utf-8")
        raise typer.Exit(0)
    _print_plan(p)
    if p.rekordbox_additions and p.rekordbox_xml:
        typer.echo(f"\n+ {len(p.rekordbox_additions)} new tracks → rekordbox '{playlist}' playlist")
    elif p.rekordbox_additions:
        typer.echo(f"\n{len(p.rekordbox_additions)} new tracks filed (pass --rekordbox-xml to also add them to rekordbox)")
    out.write_text(json.dumps(p.to_dict(), indent=2), encoding="utf-8")
    report_out.write_text(report.render(), encoding="utf-8")
    typer.echo(f"\nDry run — nothing changed. Plan: {out}   Report: {report_out}")
    typer.echo(f"Review both, then:  librarian apply {out}")


@app.command()
def apply(
    plan_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Reviewed plan JSON from `librarian plan`")],
    runs_dir: Annotated[Path, typer.Option("--runs-dir", help="Where undo journals + backups are kept")] = DEFAULT_RUNS_DIR,
    no_backup: Annotated[bool, typer.Option("--no-backup", help="Skip the pre-apply backup (only if you already have one)")] = False,
    full_backup: Annotated[bool, typer.Option("--full-backup", help="Back up the whole library, not just touched files")] = False,
) -> None:
    """Execute a reviewed plan, journaling every move so it can be undone."""
    p = Plan.from_dict(json.loads(plan_file.read_text(encoding="utf-8")))
    if not p.actions and not p.tag_edits:
        typer.echo("Plan has nothing to apply.")
        raise typer.Exit(0)
    try:
        journal = apply_plan(p, runs_dir.absolute(), backup=not no_backup, full_backup=full_backup)
    except EngineError as exc:
        typer.secho(f"Refused to apply (nothing changed): {exc}", fg="red", err=True)
        raise typer.Exit(1)
    done = sum(1 for a in journal.actions if a.status == DONE)
    retagged = sum(1 for t in journal.tag_edits if t.status == DONE)
    failed = sum(1 for t in journal.tag_edits if t.status != DONE)
    bits = []
    if done or not retagged:  # always name actions unless this was a tag-only run
        bits.append(f"{done} action{'s' if done != 1 else ''}")
    if retagged:
        bits.append(f"{retagged} tag repair{'s' if retagged != 1 else ''}")
    typer.secho(f"\nApplied {', '.join(bits)}.", fg="green")
    if failed:
        typer.secho(
            f"{failed} tag repair(s) could not be written (unsupported file?) — left unchanged.",
            fg="yellow",
        )
    if journal.backup:
        typer.echo(f"Backup ({journal.backup['mode']}): {journal.backup['files']} files in {journal.backup['dir']}")
    if journal.rekordbox and journal.rekordbox.get("rewritten"):
        typer.echo(f"rekordbox XML updated: {journal.rekordbox['original']}")
    typer.echo(f"Run id: {journal.run_id}")
    typer.echo(f"Undo with:  librarian undo {journal.run_id} --runs-dir {runs_dir}")


@app.command()
def undo(
    run_id: Annotated[str, typer.Argument(help="Run id from a previous apply")],
    runs_dir: Annotated[Path, typer.Option("--runs-dir", help="Where undo journals are kept")] = DEFAULT_RUNS_DIR,
) -> None:
    """Reverse a run completely — files and rekordbox XML back to before."""
    try:
        journal = undo_run(run_id, runs_dir.absolute())
    except EngineError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1)
    reverted = sum(1 for a in journal.actions if a.status == "reverted")
    retags = sum(1 for t in journal.tag_edits if t.status == "reverted")
    bits = []
    if reverted or not retags:
        bits.append(f"{reverted} action{'s' if reverted != 1 else ''}")
    if retags:
        bits.append(f"{retags} tag repair{'s' if retags != 1 else ''}")
    typer.secho(f"Undid run {run_id}: reversed {', '.join(bits)}.", fg="green")


@app.command()
def runs(
    runs_dir: Annotated[Path, typer.Option("--runs-dir", help="Where undo journals are kept")] = DEFAULT_RUNS_DIR,
) -> None:
    """List apply runs and their status."""
    journals = list_runs(runs_dir.absolute())
    if not journals:
        typer.echo(f"No runs recorded under {runs_dir}.")
        return
    for j in journals:
        n = len(j.actions)
        marker = "✓" if j.status == APPLIED else ("↺" if j.status == "undone" else "…")
        typer.echo(f"{marker} {j.run_id}  {j.status:8}  {n} actions  {j.library_root}")


@app.command()
def serve(
    library_root: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="Library root to manage")],
    rekordbox_xml: Annotated[Optional[Path], typer.Option("--rekordbox-xml", exists=True, dir_okay=False, help="rekordbox collection XML to keep in sync")] = None,
    runs_dir: Annotated[Optional[Path], typer.Option("--runs-dir", help="Where undo journals + backups are kept (default <root>/.librarian/runs)")] = None,
    port: Annotated[int, typer.Option("--port", help="Port for the local web app")] = 8765,
) -> None:
    """Run the local review web app (FastAPI) over this library on 127.0.0.1."""
    try:
        from .webapp.server import serve as run_server
    except ModuleNotFoundError:
        typer.secho(
            "web extra not installed — run:  pip install -e '.[web]'", fg="red", err=True
        )
        raise typer.Exit(1)
    typer.echo(f"librarian web app → http://127.0.0.1:{port}  (library: {library_root})")
    typer.echo("Dry-run first: scan, review the table, untick rows, Apply. Ctrl-C to stop.")
    run_server(
        library_root.absolute(),
        rekordbox_xml=rekordbox_xml.absolute() if rekordbox_xml else None,
        runs_dir=runs_dir.absolute() if runs_dir else None,
        port=port,
    )


if __name__ == "__main__":
    app()
