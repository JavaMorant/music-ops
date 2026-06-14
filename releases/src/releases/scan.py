"""Read-only scan of ``ProducerLibrary/projects`` into Project records.

This module **only reads**. It never moves, renames, or writes anything under
the scan root — the SQLite index it feeds lives elsewhere (see ``db.py``).

What counts as a project
------------------------
* **folder-project** — a non-structural folder that directly contains one or
  more ``.flp`` files. Multiple ``.flp`` in the same folder are versions of one
  project (e.g. ``Hong Tonky.flp`` + ``Hongky Tonky_2.flp``), grouped together.
* **standalone .flp** — a loose ``.flp`` sitting directly in a structural folder
  (the scan root, ``Tracks``, a stage folder). Each is its own project.
* **standalone bounce** — a loose render (``.mp3/.wav/...``) in a structural
  folder with no ``.flp`` beside it: a finished track with no project folder
  (the loose mp3s in ``Track List``). Kind = "bounce".

Sample packs, presets, FL templates, ``Backup/`` autosaves and ``Samples/`` are
pruned (see ``infer.EXCLUDE_NAMES``).
"""

from __future__ import annotations

import os
from pathlib import Path

from .infer import (
    EXCLUDE_NAMES,
    STRUCTURAL_NAMES,
    guess_genre,
    infer_stage,
    parse_bpm,
    parse_key,
)
from .model import Project

FLP_EXT = ".flp"
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aiff", ".aif", ".ogg"}
# Subfolders whose audio counts as a real bounce/master (not just stems/recordings).
BOUNCE_DIRS = {"exports", "export", "bounce", "bounces", "master", "masters",
               "render", "renders", "mixdown", "mixdowns", "print", "prints"}


def _is_audio(name: str) -> bool:
    return Path(name).suffix.lower() in AUDIO_EXTS


def _newest_mtime(paths: list[Path]) -> float:
    best = 0.0
    for p in paths:
        try:
            best = max(best, p.stat().st_mtime)
        except OSError:
            continue
    return best


def _has_real_bounce(folder: Path, sibling_audio: list[str]) -> bool:
    """A project 'has a bounce' if a render sits beside the .flp, or inside an
    explicitly export-named subfolder. Generic ``Audio/`` stems don't count —
    nearly every project has those, so they'd be useless as a done-signal."""
    if sibling_audio:
        return True
    for sub in BOUNCE_DIRS:
        d = folder / sub
        if d.is_dir():
            try:
                if any(_is_audio(f.name) for f in d.iterdir() if f.is_file()):
                    return True
            except OSError:
                pass
    return False


def scan(root: Path) -> list[Project]:
    """Walk ``root`` and return every project found, read-only."""
    root = root.resolve()
    projects: list[Project] = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded areas in-place so we never descend into them.
        dirnames[:] = [d for d in dirnames if d.lower() not in EXCLUDE_NAMES]

        here = Path(dirpath)
        rel_parts = list(here.relative_to(root).parts) if here != root else []
        is_structural = (here == root) or (here.name.lower() in STRUCTURAL_NAMES)

        flps = [f for f in filenames if f.lower().endswith(FLP_EXT)]
        audio = [f for f in filenames if _is_audio(f)]

        if flps and not is_structural:
            # One folder-project grouping all its .flp versions.
            flp_paths = [here / f for f in flps]
            text = " ".join([here.name, *rel_parts, *flps, *audio])
            projects.append(
                Project(
                    path=str(here),
                    name=here.name,
                    kind="project",
                    stage=infer_stage(rel_parts),
                    genre=guess_genre(text),
                    bpm=parse_bpm(text),
                    key=parse_key(text),
                    has_bounce=_has_real_bounce(here, audio),
                    flp_count=len(flps),
                    last_modified=_newest_mtime(flp_paths),
                )
            )
            # Don't descend further — a folder-project's subfolders (Audio,
            # versions) are part of it, not new projects.
            dirnames[:] = []
            continue

        if is_structural:
            # Loose .flp here → each is its own standalone project.
            for f in flps:
                fp = here / f
                text = " ".join([f, *rel_parts])
                # In a structural folder, only a same-stem render belongs to
                # *this* loose .flp — other audio files are their own projects.
                siblings = [a for a in audio if Path(a).stem.lower() == fp.stem.lower()]
                projects.append(
                    Project(
                        path=str(fp),
                        name=fp.stem,
                        kind="project",
                        stage=infer_stage(rel_parts),
                        genre=guess_genre(text),
                        bpm=parse_bpm(text),
                        key=parse_key(text),
                        has_bounce=_has_real_bounce(here, siblings),
                        flp_count=1,
                        last_modified=_newest_mtime([fp]),
                    )
                )
            # Loose renders here → standalone bounce-projects, EXCEPT a render
            # that's the same-stem sibling of a loose .flp above (that's the
            # .flp project's own bounce, already represented).
            flp_stems = {Path(f).stem.lower() for f in flps}
            for f in audio:
                fp = here / f
                if fp.stem.lower() in flp_stems:
                    continue
                text = " ".join([f, *rel_parts])
                projects.append(
                    Project(
                        path=str(fp),
                        name=fp.stem,
                        kind="bounce",
                        stage=infer_stage(rel_parts),
                        genre=guess_genre(text),
                        bpm=parse_bpm(text),
                        key=parse_key(text),
                        has_bounce=True,
                        flp_count=0,
                        last_modified=_newest_mtime([fp]),
                    )
                )

    projects.sort(key=lambda p: p.path)
    return projects
