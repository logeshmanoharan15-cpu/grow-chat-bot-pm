"""Phase 8 (implementation.md) edge-case checks.

These run offline. Retrieval-dependent cases use the local Qdrant index and
the deterministic no-API-key fallback, so they never call Groq.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.query.generate as generate_module
import src.query.pipeline as pipeline_module
from src.config import load_config
from src.ingest import load as load_module
from src.ingest.load import extract_fields, load_all
from src.query.generate import generate
from src.query.pipeline import ask
from src.query.retrieve import retrieve


def _cfg_with_temp_paths(tmp_path: Path):
    cfg = load_config()
    # These tests are offline by design: never let a developer .env key make
    # them reach Groq, and always use the local embedded Qdrant index.
    cfg.groq_api_key = ""
    cfg.qdrant = {**cfg.qdrant, "mode": "local"}
    cfg.paths = {
        **cfg.paths,
        "raw_dir": tmp_path / "raw",
        "loaded_dir": tmp_path / "loaded",
        "chunks_path": tmp_path / "chunks" / "chunks.jsonl",
        "retrieval_dir": tmp_path / "retrieval",
    }
    return cfg


def _fact_hit(page_type: str, text: str, score: float = 0.9) -> dict:
    url = f"https://groww.in/mutual-funds/{page_type}-fund-direct-growth"
    return {
        "id": f"{page_type}-facts-001",
        "score": score,
        "document": text,
        "source_url": url,
        "page_type": page_type,
        "section": "facts",
        "metadata": {"fetched_at": "2026-11-10T00:00:00+00:00"},
    }


def _trace_files(path: Path) -> list[Path]:
    return sorted(path.glob("*.json")) if path.exists() else []


# ---------------------------------------------------------------- ingestion


def test_http_error_fails_ingest_and_does_not_skip_silently(tmp_path, monkeypatch):
    cfg = _cfg_with_temp_paths(tmp_path)

    def fake_fetch(url: str, timeout: float = 30.0):
        if "elss" in url:
            return 503, ""
        return 200, "<html><body><main>Some page text.</main></body></html>"

    monkeypatch.setattr(load_module, "fetch_html", fake_fetch)
    monkeypatch.setattr(load_module.time, "sleep", lambda *_: None)
    with pytest.raises(SystemExit) as exc:
        load_all(cfg)
    assert "HTTP 503" in str(exc.value)
    assert "elss" in str(exc.value)


def test_layout_change_empty_fields_still_returns_text():
    html = "<html><body><main>About\nHDFC Large Cap Fund Direct Growth\nSome prose.</main></body></html>"
    fields = extract_fields("About\nSome prose.", "large_cap", html=html)
    assert all(value is None for value in fields.values())


def test_missing_fields_are_null_never_invented():
    fields = extract_fields("No scheme facts here.", "mid_cap")
    assert all(value is None for value in fields.values())


# ---------------------------------------------------------------- scope


def test_scheme_outside_five_pages_is_refused_with_education_link(tmp_path):
    cfg = _cfg_with_temp_paths(tmp_path)
    before = _trace_files(cfg.paths["retrieval_dir"])
    answer = ask("Expense ratio of SBI Bluechip?", cfg)
    assert answer.action == "out_of_scope"
    assert "amfi" in answer.source_url.lower()
    assert _trace_files(cfg.paths["retrieval_dir"]) == before


def test_hdfc_scheme_outside_corpus_is_refused(tmp_path):
    cfg = _cfg_with_temp_paths(tmp_path)
    answer = ask("What is the expense ratio of HDFC Small Cap?", cfg)
    assert answer.action == "out_of_scope"
    assert "not in the five ingested" in answer.text


@pytest.mark.parametrize(
    "question",
    [
        "What is the expense ratio of Axis Bluechip?",
        "ICICI Prudential Bluechip exit load?",
        "HDFC Balanced Advantage minimum SIP?",
        "HDFC Large & Mid Cap expense ratio?",
    ],
)
def test_other_amc_and_unlisted_hdfc_schemes_are_out_of_scope(tmp_path, question):
    cfg = _cfg_with_temp_paths(tmp_path)
    answer = ask(question, cfg)
    assert answer.action == "out_of_scope"
    assert _trace_files(cfg.paths["retrieval_dir"]) == []


def test_ambiguous_expense_ratio_asks_which_scheme():
    cfg = load_config()
    cfg.groq_api_key = ""
    hits = [
        _fact_hit("large_cap", "Title: HDFC Large Cap\nExpense ratio: 1.0%"),
        _fact_hit("elss", "Title: HDFC ELSS\nExpense ratio: 1.19%"),
    ]
    out = generate("What is the expense ratio?", hits, cfg)
    assert "Which scheme do you mean" in out["text"]


# ---------------------------------------------------------------- runtime errors


def test_empty_qdrant_returns_run_ingest_error(tmp_path, monkeypatch):
    cfg = _cfg_with_temp_paths(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("Empty Qdrant collection. Run ingest: load → chunk → embed")

    monkeypatch.setattr(pipeline_module, "retrieve", boom)
    answer = ask("ELSS lock-in?", cfg)
    assert answer.action == "error"
    assert "Run ingest" in answer.text


def test_changed_embedding_model_requires_reembed(tmp_path):
    cfg = _cfg_with_temp_paths(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"model": "some/other-model", "collection": cfg.qdrant["collection"]}),
        encoding="utf-8",
    )
    cfg.paths["embeddings_manifest"] = manifest
    with pytest.raises(RuntimeError, match="Re-run"):
        retrieve("ELSS lock-in?", cfg, persist=False)


def test_groq_failure_shows_error_not_numbers(monkeypatch):
    class BoomGroq:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("groq down")

    monkeypatch.setattr(generate_module, "Groq", BoomGroq)
    cfg = load_config()
    cfg.groq_api_key = "test-key"
    hits = [_fact_hit("large_cap", "Title: HDFC Large Cap\nExpense ratio: 1.0%")]
    out = generate("expense ratio", hits, cfg)
    assert "unavailable" in out["text"]
    assert "1.0" not in out["text"]


def test_weak_retrieval_does_not_invent():
    cfg = load_config()
    cfg.groq_api_key = ""
    hits = [_fact_hit("large_cap", "Title: HDFC Large Cap\nExpense ratio: 1.0%", score=0.01)]
    out = generate("expense ratio", hits, cfg)
    assert "don’t have that fact" in out["text"]
    assert "1.0" not in out["text"]
    assert out["source_url"] == cfg.links["education"]


def test_hallucinated_url_is_replaced_and_extra_sentences_trimmed(monkeypatch):
    class FakeGroq:
        def __init__(self, *args, **kwargs):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    content = (
                        "One. Two. Three. Four. Five.\n"
                        "Source: https://evil.example/hallucinated\n"
                        "Last updated from sources: 2026-01-01"
                    )
                    return SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
                    )

    monkeypatch.setattr(generate_module, "Groq", FakeGroq)
    cfg = load_config()
    cfg.groq_api_key = "test-key"
    hit = _fact_hit("large_cap", "Title: HDFC Large Cap\nExpense ratio: 1.0%")
    out = generate("expense ratio", [hit], cfg)
    assert out["source_url"] == hit["source_url"]
    assert out["text"].count(".") <= 3
    assert "evil.example" not in out["text"]


# ---------------------------------------------------------------- privacy


def test_pii_refuse_writes_no_retrieval_trace(tmp_path):
    cfg = _cfg_with_temp_paths(tmp_path)
    answer = ask("My PAN is ABCDE1234F, what is ELSS lock-in?", cfg)
    assert answer.action == "pii_refuse"
    assert _trace_files(cfg.paths["retrieval_dir"]) == []
    assert "ABCDE1234F" not in answer.text


def test_empty_question_refused_without_retrieval(tmp_path):
    cfg = _cfg_with_temp_paths(tmp_path)
    answer = ask("   ", cfg)
    assert answer.action == "refuse"
    assert _trace_files(cfg.paths["retrieval_dir"]) == []


# ---------------------------------------------------------------- policy routing


@pytest.mark.parametrize(
    "question, expected_action",
    [
        ("Should I buy HDFC Large Cap?", "refuse"),
        ("What is the 3 year return of Flexi Cap?", "factsheet"),
    ],
)
def test_advice_and_performance_skip_retrieval(tmp_path, question, expected_action):
    cfg = _cfg_with_temp_paths(tmp_path)
    answer = ask(question, cfg)
    assert answer.action == expected_action
    assert _trace_files(cfg.paths["retrieval_dir"]) == []


def test_templates_never_call_the_llm(tmp_path, monkeypatch):
    cfg = _cfg_with_temp_paths(tmp_path)

    def boom(*args, **kwargs):
        raise AssertionError("LLM path must not run for PII/advice/performance")

    monkeypatch.setattr(pipeline_module, "generate", boom)
    cases = [
        ("My PAN is ABCDE1234F, what is ELSS lock-in?", "pii_refuse"),
        ("Should I sell my ELSS?", "refuse"),
        ("CAGR of HDFC ELSS", "factsheet"),
    ]
    for question, expected in cases:
        answer = ask(question, cfg)
        assert answer.action == expected


def test_statement_question_uses_configured_help_link(tmp_path):
    cfg = _cfg_with_temp_paths(tmp_path)
    answer = ask("How to download capital-gains statement?", cfg)
    assert answer.action == "retrieve"
    assert answer.source_url == cfg.links["statements_help"]


def test_amc_page_has_no_scheme_expense_ratio(tmp_path):
    cfg = _cfg_with_temp_paths(tmp_path)
    answer = ask("What is the expense ratio of HDFC Mutual Fund?", cfg)
    assert "don’t have that fact" in answer.text
