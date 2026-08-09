"""Провайдер-независимый вызов LLM со структурированным JSON-ответом.

LLM_PROVIDER=openai   -> Chat Completions + response_format json_schema
LLM_PROVIDER=anthropic -> Messages API + output_config json_schema
                          (+ prompt caching последнего system-блока)
"""
import json

import base64

import httpx

from app.config import settings

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"


def _provider_endpoint(force_openai: bool = False):
    """Возвращает (url, api_key, is_xai) под текущий LLM_PROVIDER."""
    if not force_openai and settings.llm_provider == "xai":
        return XAI_CHAT_URL, settings.xai_api_key, True
    return OPENAI_CHAT_URL, settings.openai_api_key, False


def vision_json(
    system: str,
    user_text: str,
    image_bytes: bytes,
    schema: dict,
    model: str | None = None,
    max_tokens: int = 6000,
    force_openai: bool = False,
) -> dict:
    """Vision-запрос (текст + картинка) со структурированным JSON-ответом,
    роутится по провайдеру. force_openai=True — всегда OpenAI (для классификации)."""
    url, key, is_xai = _provider_endpoint(force_openai)
    model = model or (
        settings.generation_model if not force_openai else "gpt-5-mini"
    )
    data_url = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()
    token_field = "max_tokens" if is_xai else "max_completion_tokens"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        token_field: max(max_tokens, 3000),
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "result", "strict": True, "schema": schema},
        },
    }
    resp = httpx.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=180)
    resp.raise_for_status()
    return json.loads(resp.json()["choices"][0]["message"]["content"])


def structured_completion(
    model: str,
    system_blocks: list[str],
    user_text: str,
    schema: dict,
    max_tokens: int = 8000,
    reasoning_effort: str | None = None,
) -> dict:
    if settings.llm_provider == "anthropic":
        return _anthropic(model, system_blocks, user_text, schema, max_tokens)
    if settings.llm_provider == "xai":
        # xAI (Grok) — OpenAI-совместимый API; reasoning_effort не передаём
        return _openai(
            model, system_blocks, user_text, schema, max_tokens,
            url=XAI_CHAT_URL, api_key=settings.xai_api_key,
        )
    return _openai(model, system_blocks, user_text, schema, max_tokens, reasoning_effort)


def _openai(
    model: str,
    system_blocks: list[str],
    user_text: str,
    schema: dict,
    max_tokens: int,
    reasoning_effort: str | None = None,
    url: str = OPENAI_CHAT_URL,
    api_key: str | None = None,
) -> dict:
    api_key = api_key or settings.openai_api_key
    # у reasoning-моделей (gpt-5*) часть бюджета съедают "рассуждения": при
    # тесном лимите ответ приходит пустым с finish_reason=length. Даём запас
    # и делаем повтор с увеличенным лимитом, если контент всё же пустой.
    # reasoning_effort (minimal|low|medium|high) — главный рычаг скорости.
    is_xai = url == XAI_CHAT_URL
    token_field = "max_tokens" if is_xai else "max_completion_tokens"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "\n\n".join(system_blocks)},
            {"role": "user", "content": user_text},
        ],
        token_field: max(max_tokens, 16000),
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "result", "strict": True, "schema": schema},
        },
    }
    if reasoning_effort and not is_xai:
        payload["reasoning_effort"] = reasoning_effort
    last_finish = None
    for attempt in range(2):
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=600,
        )
        if resp.status_code == 429 and "insufficient_quota" in resp.text:
            raise RuntimeError(
                "Закінчилась квота у LLM-провайдера. Поповни баланс "
                "(OpenAI: platform.openai.com/Billing; xAI: console.x.ai)."
            )
        resp.raise_for_status()
        choice = resp.json()["choices"][0]
        content = choice["message"].get("content") or ""
        last_finish = choice.get("finish_reason")
        if content.strip():
            return json.loads(content)
        # пусто — почти всегда reasoning выел лимит; поднимаем и повторяем
        payload[token_field] = min(payload[token_field] * 2, 32000)
    raise RuntimeError(
        f"OpenAI вернул пустой ответ (finish_reason={last_finish}). "
        f"Модель {model} исчерпала лимит на reasoning."
    )


def _anthropic(
    model: str, system_blocks: list[str], user_text: str, schema: dict, max_tokens: int
) -> dict:
    from app.llm.client import get_client

    system = [{"type": "text", "text": b} for b in system_blocks]
    # последний system-блок (voice profile) кэшируется вместе со всем префиксом
    system[-1]["cache_control"] = {"type": "ephemeral"}

    response = get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": user_text}],
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)
