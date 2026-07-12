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
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from .model import RekordboxAddition

_LOCATION_PREFIX = "file://localhost"


def _match_key(path: Path) -> str:
    """A comparison key that's robust to case AND Unicode normalisation.

    macOS stores filenames decomposed (NFD) while a rekordbox Location or a
    tag-derived path may be composed (NFC); the two casefold to different
    strings, so an accented/emoji filename would otherwise fail to match and
    keep a dead Location. Normalise to NFC on both sides before casefolding.
    """
    return unicodedata.normalize("NFC", os.path.abspath(path)).casefold()


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
    resolved = {_match_key(Path(k)): Path(v).absolute() for k, v in path_map.items()}

    tree = ET.parse(xml_in)
    root = tree.getroot()
    updated = 0
    for track in root.iter("TRACK"):
        # Collection tracks carry the path in Location; location-keyed playlist
        # entries (NODE KeyType="1") carry it in Key. Rewrite whichever applies
        # so neither the collection nor those playlists end up with dead paths.
        for attr in ("Location", "Key"):
            value = track.get(attr)
            if not value or not value.startswith("file://"):
                continue
            current = _match_key(location_to_path(value))
            new = resolved.get(current)
            if new is not None:
                track.set(attr, path_to_location(new))
                updated += 1

    _atomic_write(tree, xml_out)
    return updated


def _atomic_write(tree: ET.ElementTree, xml_out: Path) -> None:
    """Write the tree via temp + os.replace — a crash mid-write must never
    corrupt the live collection."""
    xml_out.parent.mkdir(parents=True, exist_ok=True)
    tmp = xml_out.with_name(xml_out.name + ".librarian-tmp")
    tree.write(tmp, encoding="UTF-8", xml_declaration=True)
    os.replace(tmp, xml_out)


def _find_or_create_playlist(playlists: ET.Element, name: str) -> ET.Element:
    """Find a playlist NODE by name, or create a TrackID-keyed one under ROOT.

    TrackID-keyed (KeyType="0") is deliberate: playlist membership then survives
    later moves/renames with no rewrite, unlike a location-keyed list.

    Only a *top-level* (direct child of ROOT) TrackID-keyed node is reused — we
    never descend into the user's folder tree (a match could be anywhere and
    non-deterministic), and we never append TrackID keys into a location-keyed
    (KeyType="1") node of the same name, which would corrupt it.
    """
    # Find the ROOT folder node (Type="0"); create a minimal skeleton if absent.
    root_node = None
    for node in playlists:
        if node.tag == "NODE" and node.get("Type") == "0":
            root_node = node
            break
    if root_node is None:
        root_node = ET.SubElement(playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": "0"})

    for node in root_node.findall("NODE"):
        if node.get("Type") == "1" and node.get("Name") == name and node.get("KeyType") == "0":
            return node

    node = ET.SubElement(
        root_node, "NODE", {"Name": name, "Type": "1", "KeyType": "0", "Entries": "0"}
    )
    # Keep the parent folder's Count attribute honest if it has one.
    if root_node.get("Count") is not None:
        root_node.set("Count", str(len(root_node.findall("NODE"))))
    return node


def add_tracks_and_playlist(
    xml_in: Path,
    additions: list[RekordboxAddition],
    playlist_name: str | None,
    xml_out: Path | None = None,
) -> int:
    """ADD new tracks to <COLLECTION> and (optionally) to a playlist NODE.

    TrackIDs are assigned ``max(existing) + 1`` upward, computed from the live
    XML at call time. Idempotent on Location: a track whose Location is already
    in the collection is skipped, so re-applying a plan never duplicates a TRACK.
    Returns the number of tracks actually added. Writes atomically.
    """
    out = xml_out or xml_in
    tree = ET.parse(xml_in)
    root = tree.getroot()
    collection = root.find("COLLECTION")
    if collection is None:
        raise ValueError("rekordbox XML has no <COLLECTION> to add tracks to")

    existing_ids: list[int] = []
    existing_locs: set[str] = set()
    for track in collection.iter("TRACK"):
        tid = track.get("TrackID")
        if tid and tid.isdigit():
            existing_ids.append(int(tid))
        loc = track.get("Location")
        if loc and loc.startswith("file://"):
            existing_locs.add(_match_key(location_to_path(loc)))
    # Also clear any TrackIDs referenced by playlist entries (a dangling Key left
    # after a manual delete can exceed the collection's max); reusing one would
    # silently enrol the new track into that unrelated playlist.
    playlists = root.find("PLAYLISTS")
    if playlists is not None:
        for entry in playlists.iter("TRACK"):
            key = entry.get("Key")
            if key and key.isdigit():
                existing_ids.append(int(key))
    next_id = (max(existing_ids) + 1) if existing_ids else 1

    new_track_ids: list[int] = []
    for add in additions:
        key = _match_key(add.location)
        if key in existing_locs:  # already present — never duplicate a TRACK
            continue
        tid = next_id
        next_id += 1
        attrs = {
            "TrackID": str(tid),
            "Name": add.name,
            "Location": path_to_location(add.location.absolute()),
        }
        if add.artist:
            attrs["Artist"] = add.artist
        if add.genre:
            attrs["Genre"] = add.genre
        if add.total_time is not None:
            attrs["TotalTime"] = str(add.total_time)
        if add.average_bpm:
            attrs["AverageBpm"] = add.average_bpm
        if add.tonality:  # musical key — only when actually tagged
            attrs["Tonality"] = add.tonality
        if add.bitrate_kbps is not None:
            attrs["BitRate"] = str(add.bitrate_kbps)
        if add.kind:
            attrs["Kind"] = add.kind
        ET.SubElement(collection, "TRACK", attrs)  # no TEMPO/POSITION_MARK — never fabricated
        existing_locs.add(key)
        new_track_ids.append(tid)

    collection.set("Entries", str(len(collection.findall("TRACK"))))

    if playlist_name and new_track_ids:
        playlists = root.find("PLAYLISTS")
        if playlists is None:
            playlists = ET.SubElement(root, "PLAYLISTS")
        node = _find_or_create_playlist(playlists, playlist_name)
        present = {t.get("Key") for t in node.findall("TRACK")}
        for tid in new_track_ids:
            if str(tid) not in present:  # idempotent on playlist membership
                ET.SubElement(node, "TRACK", {"Key": str(tid)})
        node.set("Entries", str(len(node.findall("TRACK"))))

    _atomic_write(tree, out)
    return len(new_track_ids)


def setlist_playlist(
    xml_in: Path,
    ordered: list[RekordboxAddition],
    playlist_name: str,
    xml_out: Path | None = None,
) -> tuple[int, int]:
    """(Re)build a playlist NODE containing ``ordered`` IN ORDER.

    Unlike add_tracks_and_playlist (which playlists only newly-added tracks), a
    setplan is almost entirely tracks rekordbox already knows: this references
    EXISTING collection tracks by their TrackID (matched on Location) and adds
    only the missing ones. The playlist node uses replace semantics — its
    children are rebuilt from scratch every call — so re-export is deterministic
    and set order (the product) is exact. Never guesses metadata; attributes
    are written on a new TRACK only when present on the addition.
    """
    out = xml_out or xml_in
    tree = ET.parse(xml_in)
    root = tree.getroot()
    collection = root.find("COLLECTION")
    if collection is None:
        raise ValueError("rekordbox XML has no <COLLECTION>")

    loc_to_id: dict[str, str] = {}
    existing_ids: list[int] = []
    for track in collection.iter("TRACK"):
        tid = track.get("TrackID")
        loc = track.get("Location")
        if tid and tid.isdigit():
            existing_ids.append(int(tid))
        if tid and loc and loc.startswith("file://"):
            loc_to_id[_match_key(location_to_path(loc))] = tid
    playlists = root.find("PLAYLISTS")
    if playlists is None:
        playlists = ET.SubElement(root, "PLAYLISTS")
    # As in add_tracks_and_playlist: also reserve TrackIDs referenced by any
    # playlist entry, so a dangling Key never gets silently reused for a new track.
    for entry in playlists.iter("TRACK"):
        key = entry.get("Key")
        if key and key.isdigit():
            existing_ids.append(int(key))
    next_id = (max(existing_ids) + 1) if existing_ids else 1

    added = 0
    keys_in_order: list[str] = []
    for add in ordered:
        key = _match_key(add.location)
        tid = loc_to_id.get(key)
        if tid is None:
            tid = str(next_id)
            next_id += 1
            attrs = {
                "TrackID": tid,
                "Name": add.name,
                "Location": path_to_location(add.location.absolute()),
            }
            if add.artist:
                attrs["Artist"] = add.artist
            if add.genre:
                attrs["Genre"] = add.genre
            if add.total_time is not None:
                attrs["TotalTime"] = str(add.total_time)
            if add.average_bpm:
                attrs["AverageBpm"] = add.average_bpm
            if add.tonality:  # musical key — only when actually tagged
                attrs["Tonality"] = add.tonality
            if add.bitrate_kbps is not None:
                attrs["BitRate"] = str(add.bitrate_kbps)
            if add.kind:
                attrs["Kind"] = add.kind
            ET.SubElement(collection, "TRACK", attrs)  # no TEMPO/POSITION_MARK — never fabricated
            loc_to_id[key] = tid
            added += 1
        keys_in_order.append(tid)
    collection.set("Entries", str(len(collection.findall("TRACK"))))

    node = _find_or_create_playlist(playlists, playlist_name)
    for child in list(node):
        node.remove(child)  # replace semantics: order is the product, re-export is deterministic
    for tid in keys_in_order:
        ET.SubElement(node, "TRACK", {"Key": tid})
    node.set("Entries", str(len(keys_in_order)))

    _atomic_write(tree, out)
    return added, len(keys_in_order)
