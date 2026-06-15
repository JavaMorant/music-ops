"""The pipeline engine: add, stages, touches, due, import/export."""

from __future__ import annotations

from datetime import date

import pytest

from outreach import db, store
from outreach.model import BOOKED, CONTACTED, DEAD, LEAD, Contact


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


TODAY = date(2026, 6, 15)


def test_add_opens_a_lead_due_now(conn):
    c = store.add_contact(conn, Contact(name="Sam", org="Corsica"), today=TODAY)
    assert c.id is not None
    assert c.created_at == "2026-06-15"
    state = store.get_state(conn, c.id)
    assert state.stage == LEAD
    assert state.last_touch is None
    assert state.next_followup == "2026-06-15"  # followup_in defaults to 0


def test_add_requires_a_name(conn):
    with pytest.raises(store.StoreError):
        store.add_contact(conn, Contact(name="   "))


def test_get_contact_by_id_and_name(conn):
    c = store.add_contact(conn, Contact(name="Sam Promoter"), today=TODAY)
    assert store.get_contact(conn, str(c.id)).id == c.id
    assert store.get_contact(conn, "sam promoter").id == c.id  # case-insensitive


def test_get_contact_ambiguous_name_refuses(conn):
    store.add_contact(conn, Contact(name="Sam"), today=TODAY)
    store.add_contact(conn, Contact(name="Sam"), today=TODAY)
    with pytest.raises(store.StoreError, match="several"):
        store.get_contact(conn, "Sam")


def test_get_missing_contact_raises(conn):
    with pytest.raises(store.StoreError):
        store.get_contact(conn, "999")


def test_log_touch_advances_stage_and_schedules(conn):
    c = store.add_contact(conn, Contact(name="Sam"), today=TODAY)
    state = store.log_touch(
        conn, c.id, "sent intro", channel="email", advance_to=CONTACTED, today=TODAY
    )
    assert state.stage == CONTACTED
    assert state.last_touch == "2026-06-15"
    assert state.next_followup == "2026-06-22"  # +7 default cadence
    touches = store.recent_touches(conn, c.id)
    assert len(touches) == 1
    assert touches[0].note == "sent intro"
    assert touches[0].channel == "email"


def test_log_touch_custom_cadence_and_explicit_date(conn):
    c = store.add_contact(conn, Contact(name="Sam"), today=TODAY)
    s1 = store.log_touch(conn, c.id, "nudge", followup_in=3, today=TODAY)
    assert s1.next_followup == "2026-06-18"
    s2 = store.log_touch(conn, c.id, "again", followup_on="2026-07-01", today=TODAY)
    assert s2.next_followup == "2026-07-01"


def test_terminal_stage_clears_followup(conn):
    c = store.add_contact(conn, Contact(name="Sam"), today=TODAY)
    booked = store.log_touch(conn, c.id, "confirmed", advance_to=BOOKED, today=TODAY)
    assert booked.next_followup is None
    dead = store.set_stage(conn, c.id, DEAD)
    assert dead.next_followup is None


def test_no_followup_flag(conn):
    c = store.add_contact(conn, Contact(name="Sam"), today=TODAY)
    state = store.log_touch(conn, c.id, "left voicemail", no_followup=True, today=TODAY)
    assert state.next_followup is None


def test_log_unknown_stage_refuses(conn):
    c = store.add_contact(conn, Contact(name="Sam"), today=TODAY)
    with pytest.raises(store.StoreError, match="unknown stage"):
        store.log_touch(conn, c.id, "x", advance_to="vibing", today=TODAY)


def test_log_requires_a_note(conn):
    c = store.add_contact(conn, Contact(name="Sam"), today=TODAY)
    with pytest.raises(store.StoreError):
        store.log_touch(conn, c.id, "   ", today=TODAY)


def test_due_lists_only_owed_nonterminal(conn):
    owed = store.add_contact(conn, Contact(name="Owed"), today=TODAY)  # due today
    future = store.add_contact(conn, Contact(name="Future"), today=TODAY)
    store.log_touch(conn, future.id, "just spoke", followup_in=30, today=TODAY)
    booked = store.add_contact(conn, Contact(name="Booked"), today=TODAY)
    store.log_touch(conn, booked.id, "done", advance_to=BOOKED, today=TODAY)

    due_rows = store.due(conn, today=TODAY)
    names = {pr.contact.name for pr in due_rows}
    assert names == {"Owed"}  # future not yet owed, booked is terminal
    # owed contact shows once we cross its follow-up date
    later = store.due(conn, today=date(2026, 7, 20))
    assert {pr.contact.name for pr in later} == {"Owed", "Future"}


def test_due_orders_most_overdue_first(conn):
    a = store.add_contact(conn, Contact(name="A"), today=TODAY)
    b = store.add_contact(conn, Contact(name="B"), today=TODAY)
    store.log_touch(conn, a.id, "x", followup_on="2026-06-10", today=TODAY)
    store.log_touch(conn, b.id, "y", followup_on="2026-06-01", today=TODAY)
    rows = store.due(conn, today=TODAY)
    assert [pr.contact.name for pr in rows] == ["B", "A"]


def test_list_filter_and_funnel_order(conn):
    a = store.add_contact(conn, Contact(name="Zeb"), today=TODAY)
    b = store.add_contact(conn, Contact(name="Amy"), today=TODAY)
    store.log_touch(conn, a.id, "x", advance_to=CONTACTED, today=TODAY)
    # leads first (Amy), then contacted (Zeb)
    everyone = store.list_contacts(conn)
    assert [pr.contact.name for pr in everyone] == ["Amy", "Zeb"]
    just_contacted = store.list_contacts(conn, stage=CONTACTED)
    assert [pr.contact.name for pr in just_contacted] == ["Zeb"]


def test_list_unknown_stage_refuses(conn):
    with pytest.raises(store.StoreError):
        store.list_contacts(conn, stage="vibing")


def test_import_and_export_roundtrip(conn):
    rows = [
        {"name": "Sam", "org": "Corsica", "city": "London", "venue_capacity": "500"},
        {"name": "", "org": "skip me"},  # no name → skipped
        {"name": "Lee", "genre_fit": "garage"},
    ]
    added = store.import_contacts(conn, rows, today=TODAY)
    assert [c.name for c in added] == ["Sam", "Lee"]
    assert store.get_contact(conn, "Sam").venue_capacity == 500

    exported = store.export_rows(conn)
    assert {r["name"] for r in exported} == {"Sam", "Lee"}
    sam = next(r for r in exported if r["name"] == "Sam")
    assert sam["city"] == "London"
    assert sam["stage"] == "lead"


def test_export_import_preserves_pipeline_state(conn):
    """A backup must restore faithfully — not reset every contact to a lead."""
    a = store.add_contact(conn, Contact(name="Sam", org="Corsica"), today=TODAY)
    store.log_touch(conn, a.id, "spoke", advance_to=CONTACTED, followup_in=10, today=TODAY)
    b = store.add_contact(conn, Contact(name="Lee"), today=TODAY)
    store.log_touch(conn, b.id, "confirmed", advance_to=BOOKED, today=TODAY)  # terminal
    exported = store.export_rows(conn)

    dst = db.connect(":memory:")
    # Import on a *different* day: state must come from the backup, not the clock.
    store.import_contacts(dst, exported, today=date(2026, 12, 1))
    rows = {pr.contact.name: pr.state for pr in store.list_contacts(dst)}
    assert rows["Sam"].stage == CONTACTED
    assert rows["Sam"].next_followup == "2026-06-25"  # preserved, not reset to import day
    assert rows["Lee"].stage == BOOKED
    assert rows["Lee"].next_followup is None  # terminal stays clear
    dst.close()


def test_import_rejects_unknown_stage_before_writing(conn):
    with pytest.raises(store.StoreError, match="unknown stage"):
        store.import_contacts(conn, [{"name": "Sam", "stage": "vibing"}], today=TODAY)
    assert store.list_contacts(conn) == []  # aborted before any write
