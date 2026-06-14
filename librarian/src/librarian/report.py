"""The cleanup summary report — what the scan found and what it proposes,
written alongside the plan for the user to read before applying.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CleanupReport:
    library_root: Path
    total_files: int = 0
    # (quarantined file, keeper or None, reason)
    duplicates: list[tuple[Path, Path | None, str]] = field(default_factory=list)
    low_bitrate: list[tuple[Path, int]] = field(default_factory=list)
    missing_key: list[Path] = field(default_factory=list)
    missing_tags: list[Path] = field(default_factory=list)
    renamed: int = 0
    refiled: int = 0
    left_in_place: int = 0

    def _names(self, paths) -> list[str]:
        out = []
        for p in paths:
            try:
                out.append(str(p.relative_to(self.library_root)))
            except ValueError:
                out.append(str(p))
        return out

    def render(self) -> str:
        L: list[str] = ["# Cleanup report", "", f"Library: `{self.library_root}`", ""]
        L += [
            "## Summary",
            f"- Files scanned: {self.total_files}",
            f"- Quarantined (duplicates / suspected): {len(self.duplicates)}",
            f"- Renamed to `Artist - Title`: {self.renamed}",
            f"- Refiled into genre folders: {self.refiled}",
            f"- Left in place: {self.left_in_place}",
            f"- Low-bitrate (re-acquire): {len(self.low_bitrate)}",
            f"- Missing musical key: {len(self.missing_key)}",
            f"- Missing artist/title tags: {len(self.missing_tags)}",
            "",
        ]
        if self.duplicates:
            L.append("## Duplicates → quarantine (nothing deleted)")
            for dup, keeper, reason in self.duplicates:
                kept = f"  (kept: `{keeper.name}`)" if keeper else ""
                L.append(f"- `{dup.name}` — {reason}{kept}")
            L.append("")
        if self.low_bitrate:
            L.append("## Low bitrate — re-acquire a proper copy (never auto-downloaded)")
            for p, kbps in self.low_bitrate:
                L.append(f"- `{Path(p).name}` — {kbps} kbps")
            L.append("")
        if self.missing_key:
            L.append("## Missing key — analyse/flag, never guessed")
            L += [f"- `{n}`" for n in self._names(self.missing_key)]
            L.append("")
        if self.missing_tags:
            L.append("## Missing artist/title — named from filename instead")
            L += [f"- `{n}`" for n in self._names(self.missing_tags)]
            L.append("")
        return "\n".join(L)
