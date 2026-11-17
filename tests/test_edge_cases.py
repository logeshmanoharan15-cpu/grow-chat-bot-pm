import json
from pathlib import Path

from src.config import load_config
from src.ingest.chunk import chunk_document
from src.ingest.load import extract_fields
from src.query.generate import generate
from src.query.pipeline import ask
from src.query.safety import classify

SAMPLE_TEXT = """
HDFC Large Cap Fund Direct Growth
Equity Large Cap Very High Risk
Min. for SIP ₹100
Expense ratio Information - Expense ratio 1.02%
Exit load of 1% if redeemed within 1 year
Fund benchmark NIFTY 100 Total Return Index Scheme Information Document
"""


def test_missing_fields_are_null():
    fields = extract_fields("Hello world with no scheme facts", "large_cap")
    assert all(v is None for v in fields.values())


def test_extracted_fields_from_groww_like_text():
    fields = extract_fields(SAMPLE_TEXT, "large_cap")
    assert fields["expense_ratio"] and "1.02" in fields["expense_ratio"]
    assert fields["min_sip"] and "100" in fields["min_sip"]
    assert fields["exit_load"]
    assert fields["riskometer"]
    assert fields["benchmark"]


def test_empty_fields_still_chunk_text():
    doc = {
        "page_type": "large_cap",
        "title": "HDFC Large Cap Fund Direct Growth",
        "source_url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        "fetched_at": "2026-11-01T00:00:00+00:00",
        "text": "About the scheme. This is body text only.",
        "fields": {k: None for k in ["expense_ratio", "exit_load", "min_sip", "lock_in", "riskometer", "benchmark"]},
    }
    chunks = chunk_document(doc, "section_aware", 800, 80)
    assert any(c["id"].startswith("large_cap-body-") for c in chunks)
    assert not any(c["metadata"]["section"] == "facts" for c in chunks)


def test_unknown_scheme_safety_still_retrieve_then_pipeline_message(monkeypatch):
    r = classify("Expense ratio of SBI Bluechip?")
    assert r.action == "retrieve"


def test_pii_wins_over_factual():
    r = classify("My PAN is ABCDE1234F, what is ELSS lock-in?")
    assert r.action == "pii_refuse"


def test_advice_wins_over_fact():
    r = classify("Expense ratio of large cap, should I buy it?")
    assert r.action == "refuse"


def test_hallucinated_citation_replaced():
    hits = [
        {
            "id": "elss-facts-001",
            "score": 0.9,
            "document": "Title: HDFC ELSS\nLock-in: 3 years",
            "source_url": "https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth",
            "page_type": "elss",
            "metadata": {"fetched_at": "2026-11-01T00:00:00+00:00"},
        }
    ]
    cfg = load_config()
    cfg.groq_api_key = ""
    out = generate("ELSS lock-in?", hits, cfg)
    assert out["source_url"].startswith("https://groww.in/")


def test_generate_caps_sentences(monkeypatch):
    hits = [
        {
            "id": "x",
            "score": 0.9,
            "document": "One. Two. Three. Four. Five.",
            "source_url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
            "page_type": "large_cap",
            "metadata": {"fetched_at": "2026-11-01T00:00:00+00:00"},
        }
    ]
    cfg = load_config()
    cfg.groq_api_key = ""
    out = generate("expense ratio", hits, cfg)
    assert out["text"].count(".") <= 3


def test_pii_ask_does_not_write_retrieval(tmp_path, monkeypatch):
    cfg = load_config()
    before = set(cfg.paths["retrieval_dir"].glob("*.json")) if cfg.paths["retrieval_dir"].exists() else set()
    ans = ask("My PAN is ABCDE1234F, expense ratio?", cfg)
    after = set(cfg.paths["retrieval_dir"].glob("*.json")) if cfg.paths["retrieval_dir"].exists() else set()
    assert ans.action == "pii_refuse"
    assert after == before
