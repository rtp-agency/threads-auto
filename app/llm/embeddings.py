"""Эмбеддинги для few-shot retrieval (pgvector).

EMBEDDING_PROVIDER=openai -> text-embedding-3-small с dimensions=1024
EMBEDDING_PROVIDER=voyage -> voyage-3.5 (input_type document/query)
"""
import httpx

from app.config import settings

OPENAI_URL = "https://api.openai.com/v1/embeddings"
VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"


def embed(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """input_type: 'document' для постов, 'query' для запроса при генерации
    (используется только Voyage; OpenAI различия не делает)."""
    if settings.embedding_provider == "voyage":
        return _voyage(texts, input_type)
    return _openai(texts)


def _openai(texts: list[str]) -> list[list[float]]:
    resp = httpx.post(
        OPENAI_URL,
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={
            "model": settings.embedding_model,
            "input": texts,
            "dimensions": settings.embedding_dim,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    return [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]


def _voyage(texts: list[str], input_type: str) -> list[list[float]]:
    resp = httpx.post(
        VOYAGE_URL,
        headers={"Authorization": f"Bearer {settings.voyage_api_key}"},
        json={
            "model": settings.embedding_model,
            "input": texts,
            "input_type": input_type,
            "output_dimension": settings.embedding_dim,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    return [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]


def embed_one(text: str, input_type: str = "document") -> list[float]:
    return embed([text], input_type=input_type)[0]
