"""Tests for the LLM layer: cost estimation, specs, and fallback."""

from __future__ import annotations

import os
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from tender_agent import llm as llm_module
from tender_agent.llm import (
    LLMClient,
    ModelSpec,
    TenderRelevance,
    apply_provider_keys,
    estimate_cost,
)
from tender_agent.settings import Settings
from tender_agent.storage import Storage
from tests.conftest import make_tender

_USAGE = {"input_tokens": 12, "output_tokens": 6, "total_tokens": 18}


class _FakeStructured:
    def __init__(self, parsed: Any, fail: bool) -> None:
        self._parsed = parsed
        self._fail = fail

    async def ainvoke(self, _messages: Any) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("primary model unavailable")
        return {"raw": AIMessage(content="", usage_metadata=_USAGE), "parsed": self._parsed}


class FakeChatModel:
    """Minimal stand-in implementing the methods LLMClient calls."""

    def __init__(
        self, *, parsed: Any = None, text: str = "Стислий опис.", fail: bool = False
    ) -> None:
        self._parsed = parsed
        self._text = text
        self._fail = fail

    def with_structured_output(self, _schema: Any, *, include_raw: bool = False) -> _FakeStructured:
        return _FakeStructured(self._parsed, self._fail)

    async def ainvoke(self, _messages: Any) -> AIMessage:
        if self._fail:
            raise RuntimeError("primary model unavailable")
        return AIMessage(content=self._text, usage_metadata=_USAGE)


# ── pure helpers ────────────────────────────────────────────────────────────


def test_estimate_cost_known_model() -> None:
    cost = estimate_cost("gpt-5.4-mini", 1_000_000, 1_000_000)
    assert cost == pytest.approx(0.75 + 3.0)


def test_estimate_cost_unknown_model_is_zero() -> None:
    assert estimate_cost("mystery-model", 1000, 1000) == 0.0


def test_model_spec_parse_valid() -> None:
    spec = ModelSpec.parse("openai:gpt-5.4-mini")
    assert spec.provider == "openai"
    assert spec.model == "gpt-5.4-mini"


@pytest.mark.parametrize("bad", ["", "openai", "openai:", ":model"])
def test_model_spec_parse_invalid(bad: str) -> None:
    with pytest.raises(ValueError, match="Invalid model spec"):
        ModelSpec.parse(bad)


def test_apply_provider_keys(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "original")
    apply_provider_keys(settings)
    assert os.environ["OPENAI_API_KEY"] == "test-key"


# ── LLMClient behaviour ─────────────────────────────────────────────────────


async def test_classify_happy_path(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, storage: Storage
) -> None:
    verdict = TenderRelevance(relevant=True, category="coolant", reason="антифриз")
    monkeypatch.setattr(llm_module, "_build_model", lambda _spec: FakeChatModel(parsed=verdict))
    client = LLMClient(settings, storage)

    result = await client.classify(make_tender())
    assert result.relevant is True
    assert result.category == "coolant"

    rollup = storage.usage_rollup()
    assert any(r["model"] == "gpt-5.4-mini" and r["role"] == "classify" for r in rollup)


async def test_summarize_happy_path(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, storage: Storage
) -> None:
    monkeypatch.setattr(
        llm_module, "_build_model", lambda _spec: FakeChatModel(text="Закупівля антифризу.")
    )
    client = LLMClient(settings, storage)
    assert await client.summarize(make_tender()) == "Закупівля антифризу."


async def test_classify_falls_back_to_backup(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, storage: Storage
) -> None:
    verdict = TenderRelevance(relevant=True, category="motor_oil", reason="олива")
    # Build order: classify primary, classify backup, report primary, report backup.
    models = iter(
        [
            FakeChatModel(fail=True),  # classify primary fails
            FakeChatModel(parsed=verdict),  # classify backup succeeds
            FakeChatModel(),  # report primary
            FakeChatModel(),  # report backup
        ]
    )
    monkeypatch.setattr(llm_module, "_build_model", lambda _spec: next(models))
    client = LLMClient(settings, storage)

    result = await client.classify(make_tender())
    assert result.category == "motor_oil"
    # Usage must be attributed to the backup model (gpt-5.4-nano).
    rollup = storage.usage_rollup()
    assert any(r["model"] == "gpt-5.4-nano" for r in rollup)


async def test_classify_returns_default_when_all_models_fail(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, storage: Storage
) -> None:
    monkeypatch.setattr(llm_module, "_build_model", lambda _spec: FakeChatModel(fail=True))
    client = LLMClient(settings, storage)
    result = await client.classify(make_tender())
    assert result.relevant is False
    assert result.category == "other"
