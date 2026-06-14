"""The plan model: an Action is one reversible file operation, a Plan is a list
of them. Everything the engine executes is described here first, in full, before
a single byte moves on disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Action kinds. Both are mechanically a *move* (no copy, no re-encode, no
# delete) — the kind only records intent for the review table and the report.
MOVE = "move"            # rename / relocate a track within the library
QUARANTINE = "quarantine"  # set a duplicate or reject aside (never deleted)

ACTION_KINDS = (MOVE, QUARANTINE)


@dataclass(frozen=True)
class Action:
    """One file operation: move ``src`` to ``dest`` because of ``reason``.

    Paths are absolute. ``src`` and ``dest`` must differ. The engine guarantees
    this never overwrites an existing file, so an Action never destroys data.
    """

    kind: str
    src: Path
    dest: Path
    reason: str

    def __post_init__(self) -> None:
        if self.kind not in ACTION_KINDS:
            raise ValueError(f"unknown action kind: {self.kind!r}")
        if self.src == self.dest:
            raise ValueError(f"action src and dest are identical: {self.src}")

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "src": str(self.src),
            "dest": str(self.dest),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Action:
        return cls(
            kind=data["kind"],
            src=Path(data["src"]),
            dest=Path(data["dest"]),
            reason=data["reason"],
        )


@dataclass(frozen=True)
class RekordboxAddition:
    """A brand-new track to ADD to the rekordbox COLLECTION (and, optionally, a
    "New This Week" playlist) — what the inbox pipeline imports.

    ``location`` is the track's FINAL filed path (the matching ``Action.dest``),
    because additions are written *after* every move completes. Any metadata
    field may be None; we never guess — in particular ``tonality`` is written
    only when the source file was actually tagged with a key.
    """

    location: Path
    name: str
    artist: str | None = None
    genre: str | None = None
    total_time: int | None = None    # seconds
    average_bpm: str | None = None
    tonality: str | None = None      # musical key — only if present, never guessed
    bitrate_kbps: int | None = None
    kind: str | None = None          # e.g. "MP3 File"

    def to_dict(self) -> dict:
        return {
            "location": str(self.location),
            "name": self.name,
            "artist": self.artist,
            "genre": self.genre,
            "total_time": self.total_time,
            "average_bpm": self.average_bpm,
            "tonality": self.tonality,
            "bitrate_kbps": self.bitrate_kbps,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, d: dict) -> RekordboxAddition:
        return cls(
            location=Path(d["location"]),
            name=d["name"],
            artist=d.get("artist"),
            genre=d.get("genre"),
            total_time=d.get("total_time"),
            average_bpm=d.get("average_bpm"),
            tonality=d.get("tonality"),
            bitrate_kbps=d.get("bitrate_kbps"),
            kind=d.get("kind"),
        )


@dataclass
class Plan:
    """A reviewable set of actions over one library root.

    ``rekordbox_xml``, when set, is an exported rekordbox collection whose track
    Locations must be rewritten to follow the moves so playlists, hot cues and
    memory cues survive.

    ``location_redirects`` is where rekordbox should be pointed for each old
    path — usually a file's new path, but for a quarantined *duplicate* it's the
    kept copy's location (so cues land on the track that stays in the library,
    not on the reject). When None, the engine derives a redirect straight from
    each action's src -> dest.

    ``rekordbox_additions`` are new tracks to add to the collection (the inbox
    case); ``rekordbox_playlist`` is the playlist node they join. Both None for
    cleanup/plan, which only move existing files.
    """

    library_root: Path
    actions: list[Action]
    rekordbox_xml: Path | None = None
    location_redirects: dict[Path, Path] | None = None
    rekordbox_additions: list[RekordboxAddition] | None = None
    rekordbox_playlist: str | None = None

    def to_dict(self) -> dict:
        return {
            "library_root": str(self.library_root),
            "rekordbox_xml": str(self.rekordbox_xml) if self.rekordbox_xml else None,
            "location_redirects": (
                {str(k): str(v) for k, v in self.location_redirects.items()}
                if self.location_redirects
                else None
            ),
            "rekordbox_additions": (
                [a.to_dict() for a in self.rekordbox_additions]
                if self.rekordbox_additions
                else None
            ),
            "rekordbox_playlist": self.rekordbox_playlist,
            "actions": [a.to_dict() for a in self.actions],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Plan:
        redirects = data.get("location_redirects")
        additions = data.get("rekordbox_additions")
        return cls(
            library_root=Path(data["library_root"]),
            actions=[Action.from_dict(a) for a in data["actions"]],
            rekordbox_xml=Path(data["rekordbox_xml"]) if data.get("rekordbox_xml") else None,
            location_redirects=(
                {Path(k): Path(v) for k, v in redirects.items()} if redirects else None
            ),
            rekordbox_additions=(
                [RekordboxAddition.from_dict(a) for a in additions] if additions else None
            ),
            rekordbox_playlist=data.get("rekordbox_playlist"),
        )
