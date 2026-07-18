"""LLM extrakce faktur – pluggable endpoint (lokální SGLang/Ollama, cloud záloha).

Jádro nezná konkrétního poskytovatele: pracuje přes :class:`LLMProvider`
(Protocol). Konkrétní klient (OpenAI-kompatibilní) žije v :mod:`.providers`.
"""

from .base import (
    LLMConfig,
    LLMEndpoint,
    LLMInvoiceExtractor,
    LLMProvider,
    build_provider,
)

__all__ = [
    "LLMConfig",
    "LLMEndpoint",
    "LLMInvoiceExtractor",
    "LLMProvider",
    "build_provider",
]
