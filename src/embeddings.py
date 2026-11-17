from __future__ import annotations

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from src.config import load_config


@lru_cache(maxsize=1)
def get_model(model_name: str | None = None) -> SentenceTransformer:
    cfg = load_config()
    name = model_name or cfg.embedding["model"]
    return SentenceTransformer(name)


def embed_texts(texts: list[str], model_name: str | None = None) -> list[list[float]]:
    model = get_model(model_name)
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=len(texts) > 4)
    return [v.tolist() for v in vectors]
