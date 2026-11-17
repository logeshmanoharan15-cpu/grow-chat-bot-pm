from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.config import Config, load_config
from src.embeddings import embed_texts
from src.vector_store import (
    collection_count,
    collection_name,
    query_client,
    search,
    store_lock,
)


def detect_page_type(question: str, cfg: Config) -> str | None:
    q = (question or "").lower()
    hits = []
    for page_type, aliases in cfg.scheme_aliases.items():
        if any(alias.lower() in q for alias in aliases):
            hits.append(page_type)
    if len(hits) == 1:
        return hits[0]
    return None


def _check_manifest(cfg: Config) -> None:
    """Fail loudly when the index was built with different settings."""
    path: Path = cfg.paths["embeddings_manifest"]
    if not path.exists():
        raise RuntimeError("Embedding manifest missing. Run ingest: load → chunk → embed")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("Embedding manifest is unreadable. Re-run: python -m src.ingest.embed") from exc
    if manifest.get("model") != cfg.embedding["model"]:
        raise RuntimeError(
            "Embedding model changed in config.yaml. Re-run: python -m src.ingest.embed"
        )
    if manifest.get("collection") != collection_name(cfg):
        raise RuntimeError(
            "Qdrant collection changed in config.yaml. Re-run: python -m src.ingest.embed"
        )
    if manifest.get("backend") not in (None, "qdrant"):
        raise RuntimeError(
            "Embedding manifest was built for a different vector DB. "
            "Re-run: python -m src.ingest.embed"
        )


def retrieve(question: str, cfg: Config | None = None, persist: bool = True) -> list[dict]:
    cfg = cfg or load_config()
    _check_manifest(cfg)
    client = query_client(cfg)
    name = collection_name(cfg)
    query_vec = embed_texts([question], cfg.embedding["model"])[0]
    page_type = detect_page_type(question, cfg)
    with store_lock(cfg):
        try:
            exists = client.collection_exists(name)
        except Exception as exc:
            raise RuntimeError("Vector DB unavailable. Run: python -m src.ingest.embed") from exc
        if not exists:
            raise RuntimeError("Collection missing. Run: python -m src.ingest.embed")
        if collection_count(client, name) == 0:
            raise RuntimeError("Empty Qdrant collection. Run ingest: load → chunk → embed")

        hits = search(
            client,
            cfg,
            query_vec,
            limit=int(cfg.retrieval.get("top_k", 4)),
            page_type=page_type,
        )

    if persist:
        _write_trace(question, hits, cfg)
    return hits


def _write_trace(question: str, hits: list[dict], cfg: Config) -> Path:
    out_dir: Path = cfg.paths["retrieval_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "query": question,
        "top_k": int(cfg.retrieval.get("top_k", 4)),
        "backend": "qdrant",
        "model": cfg.embedding["model"],
        "collection": collection_name(cfg),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "hits": [
            {
                "id": h["id"],
                "score": h["score"],
                "source_url": h["source_url"],
                "page_type": h["page_type"],
                "section": h["section"],
                "preview": h["preview"],
            }
            for h in hits
        ],
    }
    path = out_dir / f"{ts}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--q", required=True)
    args = parser.parse_args()
    hits = retrieve(args.q)
    for h in hits:
        print(f"{h['score']:.3f}  {h['id']}  {h['source_url']}")
        print(f"    {h['preview'][:120]}")


if __name__ == "__main__":
    sys.exit(main())
