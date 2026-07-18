"""Kontrakt LLM vrstvy: konfigurace, Protocol poskytovatele, extraktor.

Nic tady neimportuje ``requests`` ani Django – konkrétní síťový klient je až
v :mod:`.providers` a vytváří se přes :func:`build_provider`. Díky tomu jde do
:func:`uctokit.invoices.pipeline.extract` v testech vstříknout falešný provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .. import validators as V
from ..types import ExtractedInvoice, Field, SOURCE_LLM, SOURCE_VISION
from .prompts import SYSTEM_PROMPT, VISION_USER, build_user_text

# Hrubá důvěra podle cesty (finální doladí validátory ve scoring.rescore).
BASE_TEXT_CONFIDENCE = 0.72
BASE_VISION_CONFIDENCE = 0.65


@dataclass(frozen=True)
class LLMEndpoint:
    """Jeden OpenAI-kompatibilní endpoint (lokální i cloud)."""

    base_url: str          # např. http://localhost:30000/v1
    model: str             # název textového modelu (Qwen…)
    api_key: str = ""      # u lokálních často prázdné
    vision_model: str = "" # název vision modelu; prázdné = bez vision cesty
    timeout: int = 60

    @property
    def supports_vision(self) -> bool:
        return bool(self.vision_model)


@dataclass(frozen=True)
class LLMConfig:
    """Řetěz endpointů (primární první, další jako záloha)."""

    endpoints: tuple[LLMEndpoint, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.endpoints)

    @property
    def supports_vision(self) -> bool:
        return any(e.supports_vision for e in self.endpoints)


@runtime_checkable
class LLMProvider(Protocol):
    """Minimální rozhraní poskytovatele: vrátí naparsovaný JSON dict."""

    @property
    def supports_vision(self) -> bool:
        ...

    def complete_json(
        self, *, system: str, user: str, images: list[bytes] | None = None
    ) -> dict:
        ...


def _to_field(raw_value, key: str, base: float, source: str) -> Field:
    """Znormalizuje jednu hodnotu z LLM na :class:`Field` s hrubou důvěrou."""
    if raw_value in (None, "", "null"):
        return Field()

    if key == "total_amount":
        value = V.normalize_amount(raw_value)
    elif key in ("issue_date", "due_date"):
        value = V.normalize_date(raw_value)
    elif key == "supplier_ico":
        value = V.normalize_ico(raw_value)
    elif key == "variable_symbol":
        value = V.normalize_vs(raw_value)
    elif key == "supplier_iban":
        value = V.normalize_iban(raw_value)
    elif key == "supplier_account":
        value = V.normalize_account(raw_value)
    elif key == "currency":
        value = str(raw_value).strip().upper()[:3]
    else:
        value = str(raw_value).strip()

    if value in (None, ""):
        return Field()
    return Field(value=value, confidence=base, source=source, raw=str(raw_value))


def _payload_to_invoice(payload: dict, source: str, base: float) -> ExtractedInvoice:
    payload = payload or {}
    inv = ExtractedInvoice()
    for key, _f in inv.items():
        setattr(inv, key, _to_field(payload.get(key), key, base, source))
    return inv


class LLMInvoiceExtractor:
    """Obalí :class:`LLMProvider` a vrací :class:`ExtractedInvoice`."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    @property
    def supports_vision(self) -> bool:
        return bool(getattr(self.provider, "supports_vision", False))

    def from_text(self, text: str) -> ExtractedInvoice:
        payload = self.provider.complete_json(
            system=SYSTEM_PROMPT, user=build_user_text(text)
        )
        return _payload_to_invoice(payload, SOURCE_LLM, BASE_TEXT_CONFIDENCE)

    def from_images(self, images: list[bytes]) -> ExtractedInvoice:
        payload = self.provider.complete_json(
            system=SYSTEM_PROMPT, user=VISION_USER, images=images
        )
        return _payload_to_invoice(payload, SOURCE_VISION, BASE_VISION_CONFIDENCE)


def build_provider(config: LLMConfig | None) -> LLMProvider | None:
    """Sestaví (fallback) providera z konfigurace, nebo ``None`` když je vypnutá."""
    if not config or not config.enabled:
        return None
    from .providers import FallbackProvider, OpenAICompatProvider

    providers = [OpenAICompatProvider(e) for e in config.endpoints]
    if len(providers) == 1:
        return providers[0]
    return FallbackProvider(providers)
