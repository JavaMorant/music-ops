# src/librarian/setplan/export.py
from __future__ import annotations

from pathlib import Path


def to_m3u8(plan, path: Path) -> None:
    lines = ["#EXTM3U"]
    for s in plan.slots:
        c = s.candidate
        secs = int(c.length_s or -1)
        lines.append(f"#EXTINF:{secs},{c.artist} - {c.title}".rstrip())
        lines.append(str(c.path))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def to_markdown(plan, path: Path) -> None:
    sp = plan.spec
    journey = " → ".join(f"{g} {int(f * 100)}%" for g, f in sp.journey) or "any"
    out = [f"# Set plan — {sp.minutes} min · {sp.arc} · {journey}", ""]
    for s in plan.slots:
        c = s.candidate
        key = f"{c.camelot[0]}{c.camelot[1]}" if c.camelot else "—"
        bpm = f"{int(c.bpm)}" if c.bpm else "—"
        clock = f"+{int(s.clock_min)}m"
        out.append(f"{s.index + 1:>2}. [{clock}] **{c.artist} — {c.title}**  ·  {bpm} BPM · {key}")
        out.append(f"    _{s.reason}_")
        for alt in s.alternates:
            ac = alt["candidate"]
            out.append(f"    alt: {ac.artist} — {ac.title} ({alt['reason']})")
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")
