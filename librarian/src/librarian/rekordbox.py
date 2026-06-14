"""rekordbox cue safety.

When a tracked file moves, rekordbox must learn its new path or every hot cue,
memory cue and playlist entry for that track points at a dead location. rekordbox
exports its collection as an XML where each ``<TRACK>`` carries a
``Location="file://localhost/...percent-encoded-abs-path..."`` attribute. We
rewrite exactly those Locations for the files we moved and leave everything else
— cues, playlists, beatgrids — untouched.

This uses only the standard library: the moved-path map is known, so no
rekordbox database access is needed here.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

_LOCATION_PREFIX = "file://localhost"


def path_to_location(path: Path) -> str:
    """Absolute filesystem path -> a rekordbox ``Location`` URL."""
    # rekordbox percent-encodes the path but keeps '/' as path separators.
    return _LOCATION_PREFIX + quote(str(path), safe="/")


def location_to_path(location: str) -> Path:
    """A rekordbox ``Location`` URL -> absolute filesystem path."""
    # Strip the scheme/host (file://localhost or file://) and decode.
    parts = urlsplit(location)
    return Path(unquote(parts.path))


def rewrite_locations(xml_in: Path, path_map: dict[Path, Path], xml_out: Path) -> int:
    """Rewrite TRACK Locations named in ``path_map`` (old abs path -> new).

    Returns the number of TRACK Locations updated. ``path_map`` keys/values are
    absolute paths. Tracks not in the map are left exactly as they were.
    """
    # Match on case-normalised absolute paths so a Location that differs only
    # by case (macOS volumes are case-insensitive) still updates — otherwise a
    # cue would silently keep pointing at the old path.
    resolved = {
        os.path.abspath(k).casefold(): Path(v).absolute() for k, v in path_map.items()
    }

    tree = ET.parse(xml_in)
    root = tree.getroot()
    updated = 0
    for track in root.iter("TRACK"):
        location = track.get("Location")
        if not location:
            continue
        current = os.path.abspath(location_to_path(location)).casefold()
        new = resolved.get(current)
        if new is not None:
            track.set("Location", path_to_location(new))
            updated += 1

    # Write atomically: a crash mid-write must never corrupt the live collection.
    xml_out.parent.mkdir(parents=True, exist_ok=True)
    tmp = xml_out.with_name(xml_out.name + ".librarian-tmp")
    tree.write(tmp, encoding="UTF-8", xml_declaration=True)
    os.replace(tmp, xml_out)
    return updated
