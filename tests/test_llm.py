"""Tests for the LLM layer: cost estimation, specs, and fallback."""

from __future__ import annotations

import os
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from tender_agent import llm as llm_module
from tender_agent.filters import Filters
from tender_agent.llm import (
    LLMClient,
    ModelSpec,
    TenderRelevance,
    _build_model,
    _tender_to_text,
    apply_provider_keys,
    estimate_cost,
)
from tender_agent.prozorro.models import Period, TenderItem, Unit, Value
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


async def test_failures_tracked_after_primary_fails(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, storage: Storage
) -> None:
    """After primary failure, LLMClient.failures has one entry with a valid kind."""
    verdict = TenderRelevance(relevant=True, category="motor_oil", reason="олива")
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
    await client.classify(make_tender())
    assert len(client.failures) == 1
    assert client.failures[0].kind != ""
    assert client.failures[0].message != ""


async def test_failures_tracked_when_both_models_fail(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, storage: Storage
) -> None:
    """When both primary and backup fail, failures has two entries."""
    monkeypatch.setattr(llm_module, "_build_model", lambda _spec: FakeChatModel(fail=True))
    client = LLMClient(settings, storage)
    await client.classify(make_tender())
    assert len(client.failures) == 2


async def test_failures_tracked_for_text_role(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, storage: Storage
) -> None:
    """_run_text failures are also appended to LLMClient.failures."""
    monkeypatch.setattr(llm_module, "_build_model", lambda _spec: FakeChatModel(fail=True))
    client = LLMClient(settings, storage)
    await client.summarize(make_tender())
    # Both primary and backup fail for the report role.
    assert len(client.failures) == 2


async def test_failures_empty_on_success(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, storage: Storage
) -> None:
    """No failures logged when all models succeed."""
    verdict = TenderRelevance(relevant=False, category="other", reason="тест")
    monkeypatch.setattr(llm_module, "_build_model", lambda _spec: FakeChatModel(parsed=verdict))
    client = LLMClient(settings, storage)
    await client.classify(make_tender())
    assert client.failures == []


async def test_summarize_returns_empty_when_all_models_fail(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, storage: Storage
) -> None:
    """Lines 249-257: _run_text exhausts all models and returns empty string."""
    monkeypatch.setattr(llm_module, "_build_model", lambda _spec: FakeChatModel(fail=True))
    client = LLMClient(settings, storage)
    result = await client.summarize(make_tender())
    assert result == ""


async def test_classify_parsed_none_branch(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, storage: Storage
) -> None:
    """Line 229: structured output returns parsed=None — falls back to backup then default."""

    class _NoneStructured:
        async def ainvoke(self, _messages: Any) -> dict[str, Any]:
            return {"raw": AIMessage(content="", usage_metadata=_USAGE), "parsed": None}

    class _NoneModel:
        def with_structured_output(
            self, _schema: Any, *, include_raw: bool = False
        ) -> _NoneStructured:
            return _NoneStructured()

    monkeypatch.setattr(llm_module, "_build_model", lambda _spec: _NoneModel())
    client = LLMClient(settings, storage)
    result = await client.classify(make_tender())
    # Both primary and backup return parsed=None, so falls back to default.
    assert result.relevant is False
    assert result.category == "other"


# ── _build_model provider branches ──────────────────────────────────────────


def test_build_model_gemini_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lines 87-89: 'gemini' is remapped to 'google_genai' before init_chat_model."""
    calls: list[tuple[str, str]] = []

    def fake_init(model: str, model_provider: str) -> Any:
        calls.append((model, model_provider))
        return object()

    monkeypatch.setattr(llm_module, "init_chat_model", fake_init)
    spec = ModelSpec(provider="gemini", model="gemini-pro")
    _build_model(spec)
    assert calls == [("gemini-pro", "google_genai")]


def test_build_model_google_genai_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lines 87-89: 'google-genai' is also remapped."""
    calls: list[tuple[str, str]] = []

    def fake_init(model: str, model_provider: str) -> Any:
        calls.append((model, model_provider))
        return object()

    monkeypatch.setattr(llm_module, "init_chat_model", fake_init)
    spec = ModelSpec(provider="google-genai", model="gemini-pro")
    _build_model(spec)
    assert calls == [("gemini-pro", "google_genai")]


def test_build_model_perplexity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lines 90-97: 'perplexity' provider imports ChatPerplexity."""

    class FakePerplexity:
        def __init__(self, model: str, timeout: object) -> None:
            pass

    import types

    fake_module = types.ModuleType("langchain_perplexity")
    fake_module.ChatPerplexity = FakePerplexity  # type: ignore[attr-defined]

    import sys

    monkeypatch.setitem(sys.modules, "langchain_perplexity", fake_module)

    spec = ModelSpec(provider="perplexity", model="sonar-pro")
    result = _build_model(spec)
    assert isinstance(result, FakePerplexity)


# ── _tender_to_text branches ─────────────────────────────────────────────────


def test_tender_to_text_with_description_and_value() -> None:
    """Lines 149, 155-157: description and value branches."""
    from tender_agent.prozorro.models import Tender as ProzorroTender

    tender = ProzorroTender(
        id="t1",
        tenderID="UA-T1",
        status="active.tendering",
        title="Тест",
        description="Детальний опис закупівлі",
        value=Value(amount=10000.0, currency="UAH"),
        items=[],
    )
    text = _tender_to_text(tender)
    assert "Детальний опис закупівлі" in text
    assert "10000.0 UAH" in text


def test_tender_to_text_with_delivery_window() -> None:
    """Lines 164-166: delivery_window branch."""
    from tender_agent.prozorro.models import Tender as ProzorroTender

    item = TenderItem(
        id="i1",
        description="Антифриз",
        quantity=10,
        unit=Unit(name="л"),
        deliveryDate=Period(startDate="2026-06-01", endDate="2026-06-30"),
    )
    tender = ProzorroTender(
        id="t2",
        tenderID="UA-T2",
        status="active.tendering",
        title="Тест доставки",
        items=[item],
    )
    text = _tender_to_text(tender)
    assert "Строк поставки" in text
    assert "2026-06-01" in text


# ── C1: prompt-injection delimiters & anti-injection instruction ─────────────


def test_tender_to_text_wraps_untrusted_data_in_delimiters() -> None:
    """C1: untrusted tender content is fenced inside <tender_data> delimiters."""
    text = _tender_to_text(make_tender(title="Антифриз"))
    assert text.startswith("<tender_data>")
    assert text.rstrip().endswith("</tender_data>")
    # The actual content sits between the delimiters.
    inner = text.split("<tender_data>", 1)[1].rsplit("</tender_data>", 1)[0]
    assert "Антифриз" in inner


def test_classify_system_prompt_has_anti_injection_instruction(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, storage: Storage
) -> None:
    """C1: both system prompts instruct the model to never obey embedded commands."""
    monkeypatch.setattr(llm_module, "_build_model", lambda _spec: FakeChatModel())
    client = LLMClient(settings, storage)
    for prompt in (client._classify_system, client._report_system):
        assert "<tender_data>" in prompt
        assert "Ніколи не виконуй" in prompt


def test_anti_injection_appended_to_config_prompts(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    storage: Storage,
    default_filters: Filters,
) -> None:
    """C1: the guardrail is appended to (not replacing) the config-driven prompt."""
    monkeypatch.setattr(llm_module, "_build_model", lambda _spec: FakeChatModel())
    client = LLMClient(settings, storage, default_filters)
    assert client._classify_system.startswith(default_filters.domain.classify_system_uk)
    assert "Ніколи не виконуй" in client._classify_system


# ── C2: LLM category output is validated against known categories ────────────


async def test_classify_coerces_unknown_category_to_other(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    storage: Storage,
    default_filters: Filters,
) -> None:
    """C2: a category the LLM invents is replaced with 'other'."""
    verdict = TenderRelevance(relevant=True, category="DROP TABLE", reason="x")
    monkeypatch.setattr(llm_module, "_build_model", lambda _spec: FakeChatModel(parsed=verdict))
    client = LLMClient(settings, storage, default_filters)

    result = await client.classify(make_tender())
    assert result.category == "other"


async def test_classify_keeps_known_category(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    storage: Storage,
    default_filters: Filters,
) -> None:
    """C2: a category that is in the configured set is left untouched."""
    verdict = TenderRelevance(relevant=True, category="coolant", reason="антифриз")
    monkeypatch.setattr(llm_module, "_build_model", lambda _spec: FakeChatModel(parsed=verdict))
    client = LLMClient(settings, storage, default_filters)

    result = await client.classify(make_tender())
    assert result.category == "coolant"


# ── H2: untrusted text fields are length-capped before reaching the LLM ──────


def test_tender_to_text_truncates_long_title() -> None:
    """H2: an oversized title is capped to 500 chars."""
    text = _tender_to_text(make_tender(title="А" * 10_000))
    assert "А" * 500 in text
    assert "А" * 501 not in text


def test_tender_to_text_truncates_long_description() -> None:
    """H2: an oversized description is capped to 4000 chars."""
    text = _tender_to_text(make_tender(description="Б" * 10_000))
    assert "Б" * 4000 in text
    assert "Б" * 4001 not in text


def test_tender_to_text_truncates_long_item_description() -> None:
    """H2: an oversized item description is capped to 500 chars."""
    long_item = TenderItem(id="i1", description="В" * 10_000)
    tender = make_tender()
    tender.items = [long_item]
    text = _tender_to_text(tender)
    assert "В" * 500 in text
    assert "В" * 501 not in text
