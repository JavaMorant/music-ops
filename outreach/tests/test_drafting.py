"""Template drafting: substitution, FILL markers, profile, file output."""

from __future__ import annotations

from datetime import date

import pytest

from outreach import drafting
from outreach.model import Contact, PipelineState

TODAY = date(2026, 6, 15)


def test_render_fills_known_slots_and_marks_unknown():
    tpl = "Subject: hi {{ first_name }}\n\nYou run {{ org }} ({{ genre_fit }})."
    ctx = {"first_name": "Sam", "org": "Corsica"}  # genre_fit missing
    draft = drafting.render(tpl, ctx)
    assert draft.subject == "hi Sam"
    assert "You run Corsica" in draft.body
    assert "[[ FILL: genre_fit ]]" in draft.body
    assert "genre_fit" in draft.unfilled


def test_literal_fill_prompts_count_as_unfilled():
    tpl = "Subject: x\n\nBody [[ FILL: their recent night ]] more."
    draft = drafting.render(tpl, {})
    assert "their recent night" in draft.unfilled


def test_build_context_only_includes_populated_fields():
    c = Contact(name="Sam Promoter", org="Corsica", genre_fit=None)
    ctx = drafting.build_context(c, today=TODAY)
    assert ctx["first_name"] == "Sam"
    assert ctx["name"] == "Sam Promoter"
    assert ctx["org"] == "Corsica"
    assert ctx["date"] == "2026-06-15"
    assert "genre_fit" not in ctx  # not populated → will surface as FILL


def test_build_context_keeps_zero_capacity():
    # 0 is real data, not "missing" — must not be dropped by a falsy check.
    ctx = drafting.build_context(Contact(name="Sam", venue_capacity=0), today=TODAY)
    assert ctx["venue_capacity"] == "0"


def test_profile_loading_and_lists(tmp_path):
    p = tmp_path / "profile.toml"
    p.write_text(
        '[me]\nname = "Dibsss"\nlinks = ["a", "b"]\n', encoding="utf-8"
    )
    prof = drafting.load_profile(p)
    assert prof["me.name"] == "Dibsss"
    assert prof["me.links"] == "a\nb"


def test_missing_profile_path_raises(tmp_path):
    with pytest.raises(drafting.DraftError):
        drafting.load_profile(tmp_path / "nope.toml")


def test_no_profile_is_fine():
    assert drafting.load_profile(None) == {}


def test_packaged_templates_resolve_and_render():
    c = Contact(name="Sam", org="Corsica", city="London", genre_fit="amapiano")
    for name in ("cold", "followup"):
        draft = drafting.render_draft(
            c, template=name, profile={"me.name": "Dibsss", "me.email": "x@y.z",
                                       "me.tagline": "t", "me.links": "l"}, today=TODAY
        )
        assert draft.subject
        assert "Sam" in draft.text


def test_resolve_unknown_template_raises():
    with pytest.raises(drafting.DraftError):
        drafting.resolve_template("does-not-exist")


def test_write_draft_creates_file_and_never_overwrites(tmp_path):
    c = Contact(name="Sam Promoter", org="Corsica")
    draft = drafting.RenderedDraft(subject="hi", body="hello")
    p1 = drafting.write_draft(draft, tmp_path, c, "cold", today=TODAY)
    p2 = drafting.write_draft(draft, tmp_path, c, "cold", today=TODAY)
    assert p1.name == "2026-06-15-sam-promoter-cold.md"
    assert p1 != p2  # second write gets a suffix, original untouched
    assert p1.read_text(encoding="utf-8").startswith("Subject: hi")
