"""The AI organize-spec layer, exercised with an injected fake client so no real
API call (or key) is needed. Covers the happy path, parse failure, and the
feature flag."""

from __future__ import annotations

import json

import pytest

from librarian import ai
from librarian.organize import OrganizeSpec


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _FakeClient:
    """Records the call and returns a canned response."""

    def __init__(self, text):
        self._text = text
        self.captured = None

    class _Messages:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            self.outer.captured = kwargs
            return _Resp(self.outer._text)

    @property
    def messages(self):
        return _FakeClient._Messages(self)


def test_infer_spec_parses_rules():
    payload = json.dumps({
        "summary": "Avicii to Festival, file the rest by genre.",
        "default": "by_genre",
        "rules": [
            {"field": "artist", "op": "contains", "value": "avicii",
             "action": "folder", "target": "Festival", "reason": "festival anthems"},
        ],
    })
    client = _FakeClient(payload)
    spec = ai.infer_spec("put avicii in festival, rest by genre",
                         sample_names=["Avicii - X.mp3"], genres=["Disco"], client=client)
    assert isinstance(spec, OrganizeSpec)
    assert spec.default == "by_genre"
    assert spec.rules[0].target == "Festival"
    # the instruction + library context reached the model
    assert client.captured["model"] == ai.MODEL
    assert "avicii" in client.captured["messages"][0]["content"].lower()
    assert "Disco" in client.captured["messages"][0]["content"]


def test_infer_spec_rejects_bad_json():
    with pytest.raises(ai.AIError):
        ai.infer_spec("x", client=_FakeClient("not json"))


def test_infer_spec_rejects_invalid_spec():
    bad = json.dumps({"summary": "", "default": "by_genre",
                      "rules": [{"field": "bpm", "op": "contains", "value": "x",
                                 "action": "folder", "target": "", "reason": ""}]})
    with pytest.raises(ai.AIError):
        ai.infer_spec("x", client=_FakeClient(bad))


def test_transport_failure_becomes_aierror():
    class Boom:
        @property
        def messages(self):
            class M:
                def create(self, **kw):
                    raise RuntimeError("network down")
            return M()
    with pytest.raises(ai.AIError):
        ai.infer_spec("x", client=Boom())


def test_is_available_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ai.is_available() is False
