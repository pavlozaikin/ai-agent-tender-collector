"""LLM layer: provider-agnostic models, primary/backup fallback, usage logging.

Two roles are configured independently (see settings / .env):

* ``classify`` — high-volume relevance check, structured JSON output;
* ``report``   — low-volume Ukrainian summary writing.

Each role has a primary and a backup model. If the primary call fails, the
backup is tried. Every successful call's token usage is written to the
``llm_usage`` table for later cost analysis.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

from tender_agent.logging import get_logger
from tender_agent.prozorro.models import Tender
from tender_agent.settings import Settings
from tender_agent.state import CATEGORIES
from tender_agent.storage import Storage, UsageRecord

_log = get_logger(__name__)

_T = TypeVar("_T", bound=BaseModel)

# Estimated USD price per 1,000,000 tokens, as (input, output). Used only for
# cost tracking in the llm_usage table — keep roughly in sync with provider
# pricing. Unknown models fall back to a zero estimate.
_PRICING: dict[str, tuple[float, float]] = {
    "gpt-5.5": (5.0, 30.0),
    "gpt-5.5-pro": (30.0, 180.0),
    "gpt-5.4": (2.5, 15.0),
    "gpt-5.4-mini": (0.75, 3.0),
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate the USD cost of a call. Returns 0.0 for unknown models."""
    rates = _PRICING.get(model.lower())
    if rates is None:
        return 0.0
    return input_tokens / 1_000_000 * rates[0] + output_tokens / 1_000_000 * rates[1]


def apply_provider_keys(settings: Settings) -> None:
    """Export configured provider API keys to the environment for LangChain."""
    mapping = {
        "OPENAI_API_KEY": settings.openai_api_key,
        "ANTHROPIC_API_KEY": settings.anthropic_api_key,
        "GOOGLE_API_KEY": settings.google_api_key,
        "PERPLEXITY_API_KEY": settings.perplexity_api_key,
    }
    for var, value in mapping.items():
        if value:
            os.environ[var] = value


@dataclass(slots=True, frozen=True)
class ModelSpec:
    """A parsed ``provider:model`` configuration value."""

    provider: str
    model: str

    @classmethod
    def parse(cls, spec: str) -> ModelSpec:
        provider, sep, model = spec.partition(":")
        if not sep or not provider.strip() or not model.strip():
            raise ValueError(f"Invalid model spec {spec!r}; expected 'provider:model'")
        return cls(provider.strip().lower(), model.strip())


def _build_model(spec: ModelSpec) -> BaseChatModel:
    """Instantiate a chat model for the given provider/model spec."""
    provider = spec.provider
    if provider in {"google-genai", "gemini"}:
        provider = "google_genai"
    if provider == "perplexity":
        try:
            from langchain_perplexity import ChatPerplexity
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "langchain-perplexity must be installed to use the perplexity provider"
            ) from exc
        return ChatPerplexity(model=spec.model, timeout=None)
    return init_chat_model(spec.model, model_provider=provider)


@dataclass(slots=True)
class _Role:
    """A configured LLM role with its primary and backup models."""

    name: str
    primary_spec: ModelSpec
    primary_model: BaseChatModel
    backup_spec: ModelSpec
    backup_model: BaseChatModel

    def attempts(self) -> tuple[tuple[BaseChatModel, ModelSpec, bool], ...]:
        """Models to try, in order: (model, spec, is_fallback)."""
        return (
            (self.primary_model, self.primary_spec, False),
            (self.backup_model, self.backup_spec, True),
        )


class TenderRelevance(BaseModel):
    """Structured verdict produced by the relevance classifier."""

    relevant: bool = Field(
        description="True only if the tender procures automotive-chemistry products."
    )
    category: str = Field(description="Best-fit category, one of: " + ", ".join(CATEGORIES))
    reason: str = Field(description="One short sentence in Ukrainian justifying the verdict.")


_CLASSIFY_SYSTEM = (
    "Ти класифікатор тендерів для постачальника автохімії. "
    "Автохімія охоплює: охолоджувальні рідини та антифризи; гальмівні рідини; "
    "рідини для омивача скла; моторні, індустріальні та базові оливи. "
    "Визнач, чи закупівля стосується автохімії. Будь точним: супутні товари "
    "(паливо, фільтри, запчастини, послуги) не є автохімією. "
    "Якщо тендер не стосується автохімії — relevant=false і category='other'."
)

_REPORT_SYSTEM = (
    "Ти асистент менеджера з продажу автохімії. Напиши стислий опис тендера "
    "українською мовою (1-2 речення): що закуповують, обсяг, орієнтовну "
    "вартість та строк поставки (якщо вказано). Без вступних фраз, лише суть."
)


def _tender_to_text(tender: Tender) -> str:
    """Render the tender as plain text for an LLM prompt."""
    lines: list[str] = [f"Назва: {tender.title or '—'}"]
    if tender.description:
        lines.append(f"Опис: {tender.description}")
    if tender.status:
        lines.append(f"Статус: {tender.status}")
    codes = sorted(set(tender.classification_codes()))
    if codes:
        lines.append(f"Коди ДК 021:2015: {', '.join(codes)}")
    if tender.value and tender.value.amount is not None:
        currency = tender.value.currency or ""
        lines.append(f"Очікувана вартість: {tender.value.amount} {currency}".strip())
    for item in tender.items[:25]:
        qty = ""
        if item.quantity is not None:
            unit = item.unit.name if item.unit and item.unit.name else ""
            qty = f" — {item.quantity} {unit}".rstrip()
        lines.append(f"Позиція: {item.description or '—'}{qty}")
    start, end = tender.delivery_window()
    if start or end:
        lines.append(f"Строк поставки: {start or '?'} — {end or '?'}")
    return "\n".join(lines)


class LLMClient:
    """Runs the two LLM roles with provider fallback and usage accounting."""

    def __init__(self, settings: Settings, storage: Storage) -> None:
        self._storage = storage
        self._classify = self._make_role(
            "classify", settings.llm_classify_primary, settings.llm_classify_backup
        )
        self._report = self._make_role(
            "report", settings.llm_report_primary, settings.llm_report_backup
        )

    @staticmethod
    def _make_role(name: str, primary: str, backup: str) -> _Role:
        primary_spec = ModelSpec.parse(primary)
        backup_spec = ModelSpec.parse(backup)
        return _Role(
            name=name,
            primary_spec=primary_spec,
            primary_model=_build_model(primary_spec),
            backup_spec=backup_spec,
            backup_model=_build_model(backup_spec),
        )

    async def classify(self, tender: Tender) -> TenderRelevance:
        """Classify whether a tender procures automotive chemistry."""
        return await self._run_structured(
            self._classify,
            TenderRelevance,
            _CLASSIFY_SYSTEM,
            _tender_to_text(tender),
            default=TenderRelevance(
                relevant=False, category="other", reason="Класифікацію не виконано."
            ),
        )

    async def summarize(self, tender: Tender) -> str:
        """Write a short Ukrainian summary of a tender for the report."""
        return await self._run_text(self._report, _REPORT_SYSTEM, _tender_to_text(tender))

    # ── internals ───────────────────────────────────────────────────────────
    async def _run_structured(
        self,
        role: _Role,
        schema: type[_T],
        system: str,
        user: str,
        *,
        default: _T,
    ) -> _T:
        messages = [("system", system), ("human", user)]
        for model, spec, is_fallback in role.attempts():
            try:
                structured = model.with_structured_output(schema, include_raw=True)
                out = cast(dict[str, Any], await structured.ainvoke(messages))
                raw = out.get("raw")
                self._record(role.name, spec, raw, is_fallback)
                parsed = out.get("parsed")
                if parsed is None:
                    raise ValueError("structured output returned no parsed result")
                return cast(_T, parsed)
            except Exception as exc:  # noqa: BLE001 - fall back to the backup model
                _log.warning(
                    "llm_call_failed",
                    role=role.name,
                    model=spec.model,
                    fallback=is_fallback,
                    error=str(exc),
                )
        return default

    async def _run_text(self, role: _Role, system: str, user: str) -> str:
        messages = [("system", system), ("human", user)]
        for model, spec, is_fallback in role.attempts():
            try:
                message = await model.ainvoke(messages)
                self._record(role.name, spec, message, is_fallback)
                content = message.content
                return content.strip() if isinstance(content, str) else str(content)
            except Exception as exc:  # noqa: BLE001 - fall back to the backup model
                _log.warning(
                    "llm_call_failed",
                    role=role.name,
                    model=spec.model,
                    fallback=is_fallback,
                    error=str(exc),
                )
        return ""

    def _record(
        self,
        role: str,
        spec: ModelSpec,
        message: AIMessage | None,
        fallback: bool,
    ) -> None:
        usage: dict[str, Any] = {}
        if message is not None and message.usage_metadata is not None:
            usage = dict(message.usage_metadata)
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        self._storage.record_usage(
            UsageRecord(
                provider=spec.provider,
                model=spec.model,
                role=role,
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                estimated_cost_usd=estimate_cost(spec.model, input_tokens, output_tokens),
                fallback_used=fallback,
            )
        )
