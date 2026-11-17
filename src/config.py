from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
import os

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Source:
    page_type: str
    title: str
    url: str


@dataclass
class Config:
    sources: list[Source]
    chunk: dict[str, Any]
    embedding: dict[str, Any]
    qdrant: dict[str, Any]
    retrieval: dict[str, Any]
    llm: dict[str, Any]
    paths: dict[str, Path]
    links: dict[str, str]
    ui: dict[str, Any]
    scope: dict[str, Any]
    scheme_aliases: dict[str, list[str]]
    groq_api_key: str
    groq_model: str
    raw: dict[str, Any]

    def source_urls(self) -> set[str]:
        return {s.url for s in self.sources}


def load_config(path: Path | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    cfg_path = path or (ROOT / "config.yaml")
    data = yaml.safe_load(cfg_path.read_text())
    paths = {k: (ROOT / v) for k, v in data["paths"].items()}
    sources = [Source(**s) for s in data["sources"]]
    qdrant = {
        **data["qdrant"],
        "path": str(ROOT / data["qdrant"]["path"]),
    }
    # Deployment overrides (Streamlit secrets / hosting env vars).
    for env_name, key in (
        ("QDRANT_MODE", "mode"),
        ("QDRANT_URL", "url"),
        ("QDRANT_API_KEY", "api_key"),
    ):
        value = os.getenv(env_name)
        if value:
            qdrant[key] = value
    return Config(
        sources=sources,
        chunk=data["chunk"],
        embedding=data["embedding"],
        qdrant=qdrant,
        retrieval=data["retrieval"],
        llm=data["llm"],
        paths=paths,
        links=data["links"],
        ui=data["ui"],
        scope=data.get("scope") or {},
        scheme_aliases=data.get("scheme_aliases") or {},
        groq_api_key=os.getenv("GROQ_API_KEY", ""),
        groq_model=os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"),
        raw=data,
    )
