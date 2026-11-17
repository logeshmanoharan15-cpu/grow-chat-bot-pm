from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from src.config import Config, load_config

FIELD_LABELS = {
    "expense_ratio": "Expense ratio",
    "exit_load": "Exit load",
    "min_sip": "Minimum SIP",
    "lock_in": "Lock-in",
    "riskometer": "Riskometer",
    "benchmark": "Benchmark",
    "fund_house": "Fund house",
    "category": "Category",
    "amc_incorporation_date": "AMC incorporation date",
    "sponsor": "Sponsor",
    "trustee": "Trustee",
    "managing_director": "Managing director",
    "total_aum": "Total AUM",
}


def _facts_text(doc: dict) -> str | None:
    fields = doc.get("fields") or {}
    lines = [f"Title: {doc['title']}", f"Page type: {doc['page_type']}", f"Source: {doc['source_url']}"]
    any_field = False
    for key, label in FIELD_LABELS.items():
        value = fields.get(key)
        if value:
            lines.append(f"{label}: {value}")
            any_field = True
    if not any_field:
        return None
    return "\n".join(lines)


def _split_sections(text: str) -> list[str]:
    parts = re.split(r"\n{2,}", text or "")
    return [p.strip() for p in parts if p.strip()]


def _window(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks


def chunk_document(doc: dict, strategy: str, size: int, overlap: int) -> list[dict]:
    chunks: list[dict] = []
    meta_base = {
        "source_url": doc["source_url"],
        "page_type": doc["page_type"],
        "title": doc["title"],
        "fetched_at": doc["fetched_at"],
    }
    facts = _facts_text(doc)
    if facts:
        chunks.append(
            {
                "id": f"{doc['page_type']}-facts-001",
                "text": facts,
                "metadata": {**meta_base, "section": "facts"},
            }
        )

    body = doc.get("text") or ""
    prefix = f"Title: {doc['title']}\n"
    if strategy == "recursive":
        pieces = _window(prefix + body, size, overlap)
        section = "body"
        for i, piece in enumerate(pieces, start=1):
            chunks.append(
                {
                    "id": f"{doc['page_type']}-body-{i:03d}",
                    "text": piece if piece.startswith("Title:") else prefix + piece,
                    "metadata": {**meta_base, "section": section},
                }
            )
        return chunks

    sections = _split_sections(body)
    body_n = 0
    for section_text in sections:
        wrapped = prefix + section_text
        for piece in _window(wrapped, size, overlap):
            body_n += 1
            section_name = "amc_overview" if doc["page_type"] == "amc" else "body"
            chunks.append(
                {
                    "id": f"{doc['page_type']}-body-{body_n:03d}",
                    "text": piece,
                    "metadata": {**meta_base, "section": section_name},
                }
            )
    return chunks


def chunk_all(cfg: Config | None = None) -> list[dict]:
    cfg = cfg or load_config()
    loaded_dir: Path = cfg.paths["loaded_dir"]
    out_path: Path = cfg.paths["chunks_path"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    docs = []
    for path in sorted(loaded_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        docs.append(json.loads(path.read_text(encoding="utf-8")))
    if not docs:
        raise SystemExit("No loaded JSON found. Run: python -m src.ingest.load")

    strategy = cfg.chunk.get("strategy", "section_aware")
    size = int(cfg.chunk.get("size", 800))
    overlap = int(cfg.chunk.get("overlap", 80))
    all_chunks: list[dict] = []
    for doc in docs:
        all_chunks.extend(chunk_document(doc, strategy, size, overlap))

    with out_path.open("w", encoding="utf-8") as f:
        for ch in all_chunks:
            f.write(json.dumps(ch, ensure_ascii=False) + "\n")
    return all_chunks


def main() -> None:
    chunks = chunk_all()
    print(f"Wrote {len(chunks)} chunks to data/chunks/chunks.jsonl")
    facts = [c for c in chunks if c["metadata"]["section"] == "facts"]
    print(f"  facts chunks: {len(facts)}")


if __name__ == "__main__":
    sys.exit(main())
