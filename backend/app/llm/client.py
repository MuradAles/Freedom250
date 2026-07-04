"""OpenRouter LLM client (OpenAI-compatible).

Exposes a single ``judge`` helper that asks the model for a structured JSON
object matching a given schema. Tries strict ``json_schema`` structured output
first; on any failure falls back to ``json_object`` mode + a repair retry so a
demo never dies on a formatting hiccup.
"""

from __future__ import annotations

import json
import logging

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)


class LLMNotConfigured(RuntimeError):
    """Raised when no OpenRouter API key is configured."""


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if not settings.openrouter_api_key:
        raise LLMNotConfigured("LLM not configured (set OPENROUTER_API_KEY)")
    if _client is None:
        _client = OpenAI(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
        )
    return _client


def judge(system: str, user: str, schema: dict, schema_name: str = "finding") -> dict:
    """Return a parsed JSON object from the model matching ``schema``."""
    client = _get_client()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    # Attempt 1: strict structured output.
    try:
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
            temperature=0,
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as exc:  # noqa: BLE001 - fall back on any provider/schema issue
        logger.warning("Structured-output call failed (%s); falling back to json_object", exc)

    # Attempt 2: json_object mode with schema described in the prompt.
    messages[1]["content"] = (
        f"{user}\n\nRespond ONLY with a JSON object matching this schema:\n"
        f"{json.dumps(schema)}"
    )
    resp = client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0,
    )
    content = resp.choices[0].message.content
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Attempt 3: repair — strip anything outside the outermost braces.
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end != -1:
            return json.loads(content[start : end + 1])
        raise
