from __future__ import annotations

import re

from groq import Groq

from src.config import ROOT, Config, load_config

SOURCE_LINE = re.compile(r"^Source:\s*(\S+)", re.I | re.M)
UPDATED_LINE = re.compile(r"^Last updated from sources:\s*(.+)$", re.I | re.M)

# Question keyword -> line label in a facts chunk.
FIELD_HINTS = {
    "expense": "expense ratio",
    "exit": "exit load",
    "sip": "minimum sip",
    "lock": "lock-in",
    "risk": "riskometer",
    "benchmark": "benchmark",
    "aum": "total aum",
    "fund size": "total aum",
    "incorporat": "amc incorporation date",
    "sponsor": "sponsor",
    "trustee": "trustee",
    "fund house": "fund house",
    "amc": "fund house",
    "category": "category",
}


def _sentences(text: str, n: int = 3) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    parts = [p.strip() for p in parts if p.strip()]
    return " ".join(parts[:n]).strip()


def _names_scheme(question: str, cfg: Config) -> bool:
    q = (question or "").lower()
    return any(
        alias.lower() in q
        for aliases in cfg.scheme_aliases.values()
        for alias in aliases
    )


def _fact_lines(document: str) -> list[str]:
    lines = []
    for line in (document or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("Title:", "Page type:", "Source:")):
            continue
        lines.append(line)
    return lines


def _fallback_from_hits(question: str, hits: list[dict], cfg: Config) -> dict:
    """Deterministic, grounded answer used when GROQ_API_KEY is absent."""
    q = (question or "").lower()
    wanted = [label for hint, label in FIELD_HINTS.items() if hint in q]

    # A prose chunk can outrank the compact facts chunk. When the question asks
    # for a known field, prefer a chunk that actually contains that field, and
    # never answer from unrelated facts.
    top = hits[0]
    if wanted:
        matched = None
        for hit in hits:
            doc_lower = (hit.get("document") or "").lower()
            if not any(label in doc_lower for label in wanted):
                continue
            if hit.get("section") == "facts":
                matched = hit
                break
            if matched is None:
                matched = hit
        if matched is None:
            return _not_in_pages(cfg, hits[0])
        top = matched

    lines = _fact_lines(top.get("document") or "")
    if top.get("section") == "facts":
        chosen = [ln for ln in lines if any(label in ln.lower() for label in wanted)]
        if wanted and not chosen:
            return _not_in_pages(cfg, top)
        if not chosen:
            chosen = lines
        text = ". ".join(chosen[:3]).strip()
    else:
        text = _sentences(" ".join(lines), 3)
    if text and not text.endswith((".", "!", "?")):
        text += "."
    return {
        "text": text or "I found matching page text but no Groq key is set, so I cannot rephrase it.",
        "source_url": top.get("source_url") or cfg.links["education"],
        "last_updated": (top.get("metadata") or {}).get("fetched_at", ""),
    }


def _not_in_pages(cfg: Config, top: dict | None) -> dict:
    return {
        "text": (
            "I don’t have that fact in the five ingested Groww pages. "
            "Ask about HDFC Large Cap, Flexi Cap, ELSS Tax Saver, Mid Cap, or the HDFC AMC overview."
        ),
        "source_url": cfg.links["education"],
        "last_updated": ((top or {}).get("metadata") or {}).get("fetched_at", ""),
    }


def _ambiguous_scheme(cfg: Config) -> dict:
    return {
        "text": (
            "Which scheme do you mean: HDFC Large Cap, Flexi Cap, ELSS Tax Saver, or Mid Cap? "
            "Please name the scheme and I will look up that fact."
        ),
        "source_url": cfg.links["education"],
        "last_updated": "",
    }


def generate(question: str, hits: list[dict], cfg: Config | None = None) -> dict:
    cfg = cfg or load_config()
    min_score = float(cfg.retrieval.get("min_score", 0.25))
    strong = [h for h in hits if h.get("score", 0) >= min_score]
    allowed_urls = {h["source_url"] for h in strong if h.get("source_url")}
    top = hits[0] if hits else None

    if not strong:
        return _not_in_pages(cfg, top)

    # Ambiguous "expense ratio?" with no scheme name: only answer when exactly
    # one scheme is clearly represented in the retrieved facts chunks.
    if not _names_scheme(question, cfg):
        fact_pages = {
            h.get("page_type")
            for h in strong
            if h.get("section") == "facts" and h.get("page_type")
        }
        if len(fact_pages) > 1:
            return _ambiguous_scheme(cfg)

    if not cfg.groq_api_key:
        return _fallback_from_hits(question, strong, cfg)

    system = (ROOT / "prompts" / "system.txt").read_text(encoding="utf-8")
    context_blocks = []
    for i, h in enumerate(strong, start=1):
        meta = h.get("metadata") or {}
        context_blocks.append(
            f"[{i}] id={h['id']} source_url={h.get('source_url')} "
            f"fetched_at={meta.get('fetched_at')} page_type={h.get('page_type')}\n{h.get('document')}"
        )
    user_msg = f"QUESTION:\n{question}\n\nCONTEXT:\n" + "\n\n".join(context_blocks)

    try:
        client = Groq(api_key=cfg.groq_api_key)
        resp = client.chat.completions.create(
            model=cfg.groq_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=300,
        )
        raw = (resp.choices[0].message.content or "").strip()
    except Exception:
        return {
            "text": (
                "The language model is unavailable right now. I will not invent scheme numbers. "
                "Please try again after checking GROQ_API_KEY and GROQ_MODEL."
            ),
            "source_url": (top or {}).get("source_url") or cfg.links["education"],
            "last_updated": ((top or {}).get("metadata") or {}).get("fetched_at", ""),
        }

    src_m = SOURCE_LINE.search(raw)
    upd_m = UPDATED_LINE.search(raw)
    body = SOURCE_LINE.sub("", raw)
    body = UPDATED_LINE.sub("", body).strip()
    body = _sentences(body, 3)

    source = src_m.group(1).strip() if src_m else ""
    if source not in allowed_urls:
        source = strong[0]["source_url"]
    last_updated = (upd_m.group(1).strip() if upd_m else "") or strong[0].get("metadata", {}).get(
        "fetched_at", ""
    )
    return {"text": body, "source_url": source, "last_updated": last_updated}
