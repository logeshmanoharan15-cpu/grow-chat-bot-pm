from __future__ import annotations

from dataclasses import dataclass, field

from src.config import Config, load_config
from src.query.generate import generate
from src.query.retrieve import retrieve
from src.query.safety import classify, detect_out_of_scope, is_statement_question


@dataclass
class Answer:
    text: str
    source_url: str
    last_updated: str
    action: str
    hits: list = field(default_factory=list)
    error: str | None = None


def ask(question: str, cfg: Config | None = None) -> Answer:
    cfg = cfg or load_config()
    q = (question or "").strip()
    if not q:
        return Answer(
            text="Please enter a factual question about one of the listed schemes.",
            source_url="",
            last_updated="",
            action="refuse",
        )

    safety = classify(q, cfg)
    if safety.action != "retrieve":
        last = ""
        return Answer(
            text=safety.message,
            source_url=safety.source_url,
            last_updated=last,
            action=safety.action,
            hits=[],
        )

    out_of_scope = detect_out_of_scope(q, cfg)
    if out_of_scope:
        return Answer(
            text=(
                "That scheme is not in the five ingested Groww pages, so I cannot answer from "
                "official content. Ask about HDFC Large Cap, Flexi Cap, ELSS Tax Saver, Mid Cap, "
                "or the HDFC AMC overview."
            ),
            source_url=cfg.links["education"],
            last_updated="",
            action="out_of_scope",
            hits=[],
        )

    try:
        hits = retrieve(q, cfg, persist=True)
    except RuntimeError as exc:
        return Answer(
            text=str(exc),
            source_url="",
            last_updated="",
            action="error",
            error=str(exc),
        )

    # The five pages do not contain a statement how-to, so use the configured
    # Groww help link instead of asking the LLM to invent steps.
    if is_statement_question(q):
        return Answer(
            text=(
                "Capital-gains and account statements are downloaded from your Groww account help flow. "
                "This assistant cannot access your holdings and does not need PAN or account numbers."
            ),
            source_url=cfg.links["statements_help"],
            last_updated="",
            action="retrieve",
            hits=hits,
        )

    gen = generate(q, hits, cfg)
    return Answer(
        text=gen["text"],
        source_url=gen["source_url"],
        last_updated=gen["last_updated"],
        action="retrieve",
        hits=hits,
    )
