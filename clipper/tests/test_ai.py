"""Tests for the ambition-tier AI captioner (ai.py).

The Anthropic SDK and an API key are not present in CI, so these cover the
graceful-degradation path, prompt building, response parsing, and the call path
via an injected fake client — no network, no real key.
"""

from __future__ import annotations

import json

import pytest

from clipper import ai


def test_is_available_false_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ai.is_available() is False


def test_build_user_prompt_includes_each_clip():
    prompt = ai._build_user_prompt(
        [
            {"index": 1, "timestamp": "00:12:03", "duration": 30, "score": 0.84, "transcript": "drop incoming"},
            {"index": 2, "timestamp": "00:15:03", "duration": 30, "score": 0.82, "transcript": ""},
        ]
    )
    assert "Clip 1:" in prompt and "Clip 2:" in prompt
    assert "00:12:03" in prompt
    assert "drop incoming" in prompt
    assert "(none)" in prompt  # empty transcript rendered explicitly


def test_parse_valid_response():
    text = json.dumps(
        {
            "clips": [
                {"index": 1, "caption": "peak time", "hashtags": ["#amapiano", "#dj"], "rationale": "the drop hits"},
            ]
        }
    )
    out = ai._parse(text)
    assert len(out) == 1
    assert out[0].index == 1
    assert out[0].caption == "peak time"
    assert out[0].hashtags == ["#amapiano", "#dj"]


def test_parse_malformed_raises_ai_error():
    with pytest.raises(ai.AIError):
        ai._parse("not json at all")
    with pytest.raises(ai.AIError):
        ai._parse(json.dumps({"wrong_key": []}))


class _FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, text):
        self._text = text
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, text):
        self.messages = _FakeMessages(text)


def test_caption_clips_with_injected_client():
    text = json.dumps(
        {"clips": [{"index": 1, "caption": "c", "hashtags": ["#x"], "rationale": "r"}]}
    )
    client = _FakeClient(text)
    out = ai.caption_clips([{"index": 1, "timestamp": "00:00:01", "duration": 30, "score": 0.5}], client=client)
    assert out[0].caption == "c"
    # uses the project-mandated model and demands json_schema output
    assert client.messages.kwargs["model"] == "claude-sonnet-4-6"
    assert client.messages.kwargs["output_config"]["format"]["type"] == "json_schema"


def test_caption_clips_wraps_sdk_errors():
    class _Boom:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("network down")

    with pytest.raises(ai.AIError):
        ai.caption_clips([{"index": 1}], client=_Boom())
