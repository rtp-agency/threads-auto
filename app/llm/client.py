from functools import lru_cache

import anthropic

from app.config import settings


@lru_cache
def get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)
