from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.config import Config, load_config
from src.embeddings import embed_texts, get_model
from src.vector_store import (
    build_points,
    collection_count,
    collection_name,
    ensure_collection,
    is_local,
    location,
    open_client,
    store_lock,
)

UPSERT_BATCH = 256


def _read_chunks(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit("No chunks file. Run: python -m src.ingest.chunk")
    chunks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            chunks.append(json.loads(line))
    if not chunks:
        raise SystemExit("chunks.jsonl is empty")
    return chunks


def _batch(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def embed_all(cfg: Config | None = None) -> dict:
    cfg = cfg or load_config()
    chunks = _read_chunks(cfg.paths["chunks_path"])
    texts = [c["text"] for c in chunks]
    ids = [c["id"] for c in chunks]
    vectors = embed_texts(texts, cfg.embedding["model"])

    model = get_model(cfg.embedding["model"])
    dim = int(getattr(model, "get_sentence_embedding_dimension", lambda: len(vectors[0]))())
    name = collection_name(cfg)

    client = open_client(cfg)
    with store_lock(cfg):
        ensure_collection(client, name, dim)
        points = build_points(chunks, vectors)
        for batch in _batch(points, UPSERT_BATCH):
            client.upsert(collection_name=name, points=batch, wait=True)
        count = collection_count(client, name)

    manifest = {
        "backend": "qdrant",
        "mode": "local" if is_local(cfg) else "server",
        "location": location(cfg),
        "model": cfg.embedding["model"],
        "dim": dim,
        "collection": name,
        "count": count,
        "ids": ids,
        "embedded_at": datetime.now(timezone.utc).isoformat(),
    }
    man_path: Path = cfg.paths["embeddings_manifest"]
    man_path.parent.mkdir(parents=True, exist_ok=True)
    man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    manifest = embed_all()
    print(
        f"Embedded {manifest['count']} chunks into Qdrant collection "
        f"{manifest['collection']} at {manifest['location']} "
        f"(dim={manifest['dim']}, model={manifest['model']})"
    )


if __name__ == "__main__":
    sys.exit(main())
