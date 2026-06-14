"""The pure plan-subset helper: ticking/unticking rows must never leave the
rekordbox side of a Plan referencing a path no kept action reaches."""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from librarian.model import MOVE, QUARANTINE, Action, Plan, RekordboxAddition
from librarian.select import select_actions


def _cleanup_like(root: Path) -> Plan:
    """A cleanup-shaped plan: a dense location_redirects map (every action's src
    -> dest) plus a dup-keeper override, and one rekordbox addition."""
    a0 = Action(MOVE, root / "keeper.mp3", root / "House" / "Keep.mp3", "rename keeper")
    a1 = Action(QUARANTINE, root / "dup.mp3", root / "_quarantine" / "dup.mp3", "exact duplicate")
    a2 = Action(MOVE, root / "lonely.mp3", root / "Pop" / "Lonely.mp3", "rename")
    redirects = {a0.src: a0.dest, a1.src: a1.dest, a2.src: a2.dest}
    redirects[a1.src] = a0.dest  # dup-keeper override: dup's cues -> keeper's dest
    return Plan(library_root=root, actions=[a0, a1, a2], rekordbox_xml=root / "rb.xml",
                location_redirects=redirects)


def test_full_subset_is_identity(tmp_path: Path):
    plan = _cleanup_like(tmp_path)
    out = select_actions(plan, range(len(plan.actions)))
    assert out.actions == plan.actions
    assert out.location_redirects == plan.location_redirects


def test_empty_subset(tmp_path: Path):
    plan = _cleanup_like(tmp_path)
    out = select_actions(plan, [])
    assert out.actions == []
    assert out.location_redirects is None
    assert out.rekordbox_additions is None
    assert out.rekordbox_playlist is None


def test_out_of_range_raises(tmp_path: Path):
    plan = _cleanup_like(tmp_path)
    with pytest.raises(ValueError, match="out of range"):
        select_actions(plan, [0, 99])


def test_drop_action_drops_its_dense_redirect(tmp_path: Path):
    """Case (a): a dropped action's redirect (keyed on its src) must go."""
    plan = _cleanup_like(tmp_path)
    out = select_actions(plan, [0, 1])  # drop a2 (lonely)
    assert plan.actions[2].src not in (out.location_redirects or {})
    # kept actions' redirects survive
    assert plan.actions[0].src in out.location_redirects


def test_keep_dup_drop_keeper_drops_redirect(tmp_path: Path):
    """Case (c): keep the dup quarantine, untick the keeper move -> the
    dup->keeper_dest redirect must be dropped (its target is never reached)."""
    plan = _cleanup_like(tmp_path)
    out = select_actions(plan, [1])  # keep only the dup quarantine
    # The dup's redirect pointed at the keeper's dest, which no kept action reaches.
    assert (out.location_redirects or {}).get(plan.actions[1].src) is None


def test_keeper_left_in_place_redirect_survives(tmp_path: Path):
    """If a dup points at a keeper that has NO move action (untouched file), the
    redirect target is a real path and must survive."""
    root = tmp_path
    keeper = root / "Already Clean.mp3"
    dup = root / "dup.mp3"
    a = Action(QUARANTINE, dup, root / "_quarantine" / "dup.mp3", "exact duplicate")
    plan = Plan(library_root=root, actions=[a], rekordbox_xml=root / "rb.xml",
                location_redirects={dup: keeper})  # keeper untouched, still on disk
    out = select_actions(plan, [0])
    assert out.location_redirects == {dup: keeper}


def test_drop_import_drops_addition_and_playlist(tmp_path: Path):
    """Case (b): an inbox import's addition is keyed on the move dest; dropping
    the move drops the addition, and an empty addition set nulls the playlist."""
    root = tmp_path
    imp = Action(MOVE, root / "Inbox" / "new.mp3", root / "House" / "A - B.mp3", "import")
    other = Action(MOVE, root / "Inbox" / "two.mp3", root / "Pop" / "C - D.mp3", "import")
    adds = [
        RekordboxAddition(location=imp.dest, name="A - B"),
        RekordboxAddition(location=other.dest, name="C - D"),
    ]
    plan = Plan(library_root=root, actions=[imp, other], rekordbox_xml=root / "rb.xml",
                rekordbox_additions=adds, rekordbox_playlist="New This Week")
    out = select_actions(plan, [1])  # drop the first import
    assert len(out.rekordbox_additions) == 1
    assert out.rekordbox_additions[0].location == other.dest
    assert out.rekordbox_playlist == "New This Week"
    # Dropping everything nulls the playlist.
    none = select_actions(plan, [])
    assert none.rekordbox_additions is None and none.rekordbox_playlist is None


def test_inbox_within_drop_redirect_cross_action(tmp_path: Path):
    """The real inbox shape: a QUARANTINE action's src redirects to a DIFFERENT
    MOVE action's dest (the kept copy). This is the exact pattern target_ok
    exists for — keep both → redirect survives; drop the move → redirect drops."""
    root = tmp_path
    move = Action(MOVE, root / "Inbox" / "a.mp3", root / "House" / "A - a.mp3", "import")
    quar = Action(QUARANTINE, root / "Inbox" / "b.mp3", root / "_quarantine" / "b.mp3", "dup")
    plan = Plan(library_root=root, actions=[quar, move], rekordbox_xml=root / "rb.xml",
                location_redirects={quar.src: move.dest})  # dup's cues -> kept copy
    both = select_actions(plan, [0, 1])
    assert both.location_redirects == {quar.src: move.dest}
    # Drop the move (keep only the quarantine) -> redirect target unreachable -> dropped.
    only_quar = select_actions(plan, [0])
    assert (only_quar.location_redirects or {}) == {}


def test_redirect_target_that_is_a_kept_src_is_dropped(tmp_path: Path):
    """A redirect whose target is a path a KEPT action moves away from would be
    a dead pointer after apply, so it must be dropped."""
    root = tmp_path
    a = Action(MOVE, root / "old.mp3", root / "renamed.mp3", "rename")
    b = Action(QUARANTINE, root / "y.mp3", root / "_quarantine" / "y.mp3", "dup")
    # Pathological redirect: y -> old.mp3 (a path action 'a' is about to empty).
    plan = Plan(library_root=root, actions=[a, b], location_redirects={b.src: a.src})
    out = select_actions(plan, [0, 1])
    assert (out.location_redirects or {}) == {}, "target is emptied by a kept move"


def test_norm_matching_nfc_nfd(tmp_path: Path):
    """A redirect keyed with an NFD src must match an NFC kept action src."""
    root = tmp_path
    nfc = root / (unicodedata.normalize("NFC", "Aseréjé") + ".mp3")
    nfd = root / (unicodedata.normalize("NFD", "Aseréjé") + ".mp3")
    a = Action(MOVE, nfc, root / "Pop" / "x.mp3", "rename")
    plan = Plan(library_root=root, actions=[a], location_redirects={nfd: a.dest})
    out = select_actions(plan, [0])
    # The NFD-keyed redirect is recognised as belonging to the NFC kept action.
    assert out.location_redirects == {nfd: a.dest}
