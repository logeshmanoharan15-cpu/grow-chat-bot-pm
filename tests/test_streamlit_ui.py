"""Streamlit UI smoke tests (offline, embedded Qdrant).

These run the real app script through Streamlit's AppTest harness, so the
deployment entry point is covered without a browser.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _boot(monkeypatch) -> AppTest:
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("QDRANT_MODE", "local")
    at = AppTest.from_file("src/ui/streamlit_app.py", default_timeout=120)
    at.run()
    assert not at.exception
    return at


def _ask(at: AppTest, question: str) -> None:
    at.text_input[0].set_value(question)
    next(button for button in at.button if button.label == "Send").click().run()
    assert not at.exception


def test_streamlit_factual_answer(monkeypatch):
    at = _boot(monkeypatch)
    baseline_expanders = len(at.expander)  # Help Centre is always present
    _ask(at, "What is the lock-in for HDFC ELSS Tax Saver?")
    assert any("Lock-in: 3 years." in item.value for item in at.markdown)
    assert any("Last updated from sources:" in item.value for item in at.caption)
    assert len(at.expander) > baseline_expanders


def test_streamlit_refuses_advice_and_pii(monkeypatch):
    from src.config import load_config

    cfg = load_config()
    before = set(cfg.paths["retrieval_dir"].glob("*.json"))

    at = _boot(monkeypatch)
    baseline_expanders = len(at.expander)  # Help Centre only; no RAG trace
    _ask(at, "Should I buy HDFC Large Cap?")
    assert any("investment advice" in item.value for item in at.markdown)
    assert len(at.expander) == baseline_expanders

    _ask(at, "My PAN is ABCDE1234F, what is ELSS lock-in?")
    assert any("personal details" in item.value for item in at.markdown)
    assert len(at.expander) == baseline_expanders

    after = set(cfg.paths["retrieval_dir"].glob("*.json"))
    assert after == before
