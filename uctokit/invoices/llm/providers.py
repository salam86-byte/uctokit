"""Konkrétní LLM klienti.

:class:`OpenAICompatProvider` mluví OpenAI-kompatibilním chat rozhraním
(``/chat/completions``) – to umí lokální SGLang, Ollama (``/v1``), vLLM i cloud.
Vision jde stejným kanálem přes ``image_url`` s data-URI. ``requests`` je jediná
externí závislost a importuje se líně.
"""

from __future__ import annotations

import base64
import json
import re

from .base import LLMEndpoint, LLMProvider

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_model_json(content: str) -> dict:
    """Vytáhne JSON objekt z odpovědi modelu (i když ho obalí textem/```)."""
    if not content:
        return {}
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    match = _JSON_OBJECT_RE.search(content)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _data_uri(image: bytes, media_type: str = "image/png") -> str:
    encoded = base64.b64encode(image).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


class OpenAICompatProvider:
    """OpenAI-kompatibilní chat endpoint (SGLang / Ollama / vLLM / cloud)."""

    def __init__(self, endpoint: LLMEndpoint):
        self.endpoint = endpoint

    @property
    def supports_vision(self) -> bool:
        return self.endpoint.supports_vision

    def complete_json(
        self, *, system: str, user: str, images: list[bytes] | None = None
    ) -> dict:
        import requests  # líný import – jádro nemusí requests mít, dokud LLM neběží

        ep = self.endpoint
        if images:
            model = ep.vision_model or ep.model
            content = [{"type": "text", "text": user}]
            for img in images:
                content.append(
                    {"type": "image_url", "image_url": {"url": _data_uri(img)}}
                )
            user_message = {"role": "user", "content": content}
        else:
            model = ep.model
            user_message = {"role": "user", "content": user}

        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, user_message],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if ep.api_key:
            headers["Authorization"] = f"Bearer {ep.api_key}"

        url = ep.base_url.rstrip("/") + "/chat/completions"
        resp = requests.post(url, json=payload, headers=headers, timeout=ep.timeout)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return parse_model_json(content)


class FallbackProvider:
    """Zkusí poskytovatele po řadě; první úspěch vyhrává.

    Pro vision přeskočí ty, co ho neumí. Když všichni selžou, propadne výjimka
    posledního – pipeline ji zachytí a spadne na heuristiky.
    """

    def __init__(self, providers: list[LLMProvider]):
        self.providers = providers

    @property
    def supports_vision(self) -> bool:
        return any(p.supports_vision for p in self.providers)

    def complete_json(
        self, *, system: str, user: str, images: list[bytes] | None = None
    ) -> dict:
        candidates = self.providers
        if images:
            candidates = [p for p in self.providers if p.supports_vision]
        last_error: Exception | None = None
        for provider in candidates:
            try:
                return provider.complete_json(system=system, user=user, images=images)
            except Exception as exc:  # síť/timeout/parse – zkus dalšího
                last_error = exc
        if last_error:
            raise last_error
        return {}
