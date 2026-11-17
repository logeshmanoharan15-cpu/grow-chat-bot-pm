"""Qdrant vector-store helpers shared by ingest and query.

Two modes are supported (see `config.yaml -> qdrant`):

* ``local``  — embedded Qdrant on disk (default, no server needed).
* ``server`` — a running Qdrant instance reached over HTTP.

Both modes use the same collection schema: one point per chunk, the chunk text
stored as ``payload["document"]`` and the chunk metadata stored as flat payload
fields. Point ids are deterministic UUIDv5 values derived from the chunk id,
because Qdrant only accepts UUIDs or unsigned integers as ids.
"""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient, models

from src.config import Config

# Payload keys copied from chunk metadata into every point.
METADATA_KEYS = ("source_url", "page_type", "title", "fetched_at", "section")

_UUID_NAMESPACE = uuid.UUID("6f6d2f1e-7d5a-4b8f-9a4e-3c2b1d0e9f8a")

# Embedded Qdrant uses SQLite for persistence. FastAPI runs sync endpoints in a
# thread pool, so serialize local-mode access and relax SQLite's thread check.
_LOCAL_LOCK = threading.RLock()


def _mode(cfg: Config) -> str:
    return str(cfg.qdrant.get("mode") or "local").lower()


def is_local(cfg: Config) -> bool:
    return _mode(cfg) != "server"


def collection_name(cfg: Config) -> str:
    return str(cfg.qdrant["collection"])


def storage_path(cfg: Config) -> Path:
    return Path(cfg.qdrant["path"])


def location(cfg: Config) -> str:
    """Human-readable location used in the manifest."""
    return str(storage_path(cfg)) if is_local(cfg) else str(cfg.qdrant["url"])


@lru_cache(maxsize=4)
def _local_client(path: str) -> QdrantClient:
    # A single long-lived client per path keeps the local-mode file lock stable
    # for the lifetime of the query process.
    return QdrantClient(path=path, force_disable_check_same_thread=True)


@lru_cache(maxsize=4)
def _server_client(url: str, api_key: str | None) -> QdrantClient:
    return QdrantClient(url=url, api_key=api_key)


def open_client(cfg: Config) -> QdrantClient:
    """Return a client for ingest work.

    Local mode reuses the cached client so ingest and query never fight over
    the embedded folder lock inside one process.
    """
    if is_local(cfg):
        path = storage_path(cfg)
        path.mkdir(parents=True, exist_ok=True)
        return _local_client(str(path))
    return _server_client(
        str(cfg.qdrant["url"]), cfg.qdrant.get("api_key") or None
    )


@contextmanager
def store_lock(cfg: Config):
    """Serialize embedded-mode operations; no-op for a Qdrant server."""
    if is_local(cfg):
        with _LOCAL_LOCK:
            yield
    else:
        yield


def query_client(cfg: Config) -> QdrantClient:
    """Return the cached client used by the query path."""
    if not is_local(cfg):
        return _server_client(
            str(cfg.qdrant["url"]), cfg.qdrant.get("api_key") or None
        )
    path = storage_path(cfg)
    if not path.exists():
        raise RuntimeError(
            "Vector DB not found. Run: python -m src.ingest.embed"
        )
    return _local_client(str(path))


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_UUID_NAMESPACE, chunk_id))


def index_ready(cfg: Config) -> bool:
    """True when the configured collection exists and has at least one point."""
    try:
        client = query_client(cfg)
    except RuntimeError:
        return False
    name = collection_name(cfg)
    with store_lock(cfg):
        if not client.collection_exists(name):
            return False
        return collection_count(client, name) > 0


def point_payload(chunk: dict) -> dict[str, Any]:
    metadata = chunk.get("metadata") or {}
    return {
        "chunk_id": chunk["id"],
        "document": chunk["text"],
        **{key: metadata[key] for key in METADATA_KEYS if key in metadata},
    }


def build_points(chunks: list[dict], vectors: list[list[float]]) -> list[models.PointStruct]:
    return [
        models.PointStruct(id=point_id(chunk["id"]), vector=vector, payload=point_payload(chunk))
        for chunk, vector in zip(chunks, vectors)
    ]


def ensure_collection(client: QdrantClient, name: str, dim: int) -> None:
    """Recreate the collection so stale chunks disappear on full re-ingest."""
    if client.collection_exists(name):
        client.delete_collection(name)
    client.create_collection(
        collection_name=name,
        vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
    )
    # A Qdrant server refuses payload filters on unindexed fields; local mode
    # tolerates it. Create the index so server/deployment mode behaves the same.
    client.create_payload_index(
        collection_name=name,
        field_name="page_type",
        field_schema=models.PayloadSchemaType.KEYWORD,
        wait=True,
    )


def collection_count(client: QdrantClient, name: str) -> int:
    return int(client.count(collection_name=name, exact=True).count)


def search(
    client: QdrantClient,
    cfg: Config,
    vector: list[float],
    limit: int,
    page_type: str | None = None,
) -> list[dict]:
    """Cosine similarity search; returns hits for the query pipeline."""
    query_filter = None
    if page_type:
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="page_type", match=models.MatchValue(value=page_type)
                )
            ]
        )

    if hasattr(client, "query_points"):
        response = client.query_points(
            collection_name=collection_name(cfg),
            query=vector,
            limit=limit,
            with_payload=True,
            query_filter=query_filter,
        )
        points = response.points
    else:  # older qdrant-client releases
        points = client.search(
            collection_name=collection_name(cfg),
            query_vector=vector,
            limit=limit,
            with_payload=True,
            query_filter=query_filter,
        )

    hits = []
    for point in points:
        payload = point.payload or {}
        document = payload.get("document") or ""
        hits.append(
            {
                "id": payload.get("chunk_id") or str(point.id),
                "score": round(float(point.score), 4),
                "document": document,
                "metadata": {k: payload[k] for k in METADATA_KEYS if k in payload},
                "source_url": payload.get("source_url", ""),
                "page_type": payload.get("page_type", ""),
                "section": payload.get("section", ""),
                "preview": document[:220],
            }
        )
    return hits
