from __future__ import annotations

import html as html_lib
import os
import sys
from pathlib import Path

# `streamlit run src/ui/streamlit_app.py` puts `src/ui/` on sys.path, not the
# project root, so `import src.*` fails when the app is launched from another
# working directory (or on Streamlit Community Cloud). Add the repo root first.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from src.config import load_config
from src.ingest.embed import embed_all
from src.query.pipeline import Answer, ask
from src.vector_store import index_ready

st.set_page_config(
    page_title="HDFC MF Assistant",
    page_icon="💠",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _inject_theme() -> None:
    """Load the single CSS theme file (all visual styling lives there)."""
    css_path = Path(__file__).with_name("theme.css")
    if css_path.exists():
        st.markdown(f"<style>{css_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


_inject_theme()


def _bootstrap_secrets() -> None:
    """Copy Streamlit secrets into env vars so deployed apps can configure Groq/Qdrant."""
    try:
        secrets = st.secrets
    except Exception:
        return
    for key in (
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "QDRANT_MODE",
        "QDRANT_URL",
        "QDRANT_API_KEY",
    ):
        try:
            value = secrets[key]
        except Exception:
            continue
        if value and not os.getenv(key):
            os.environ[key] = str(value)


_bootstrap_secrets()


@st.cache_resource(show_spinner=False)
def _config():
    return load_config()


@st.cache_resource(show_spinner="Preparing the Qdrant index…")
def _prepare_index() -> tuple[bool, str]:
    """Build the index from committed chunks when it is missing (fresh deployment)."""
    cfg = _config()
    if index_ready(cfg):
        return True, ""
    chunks = cfg.paths["chunks_path"]
    if not chunks.exists():
        return (
            False,
            "No chunks found. Run `python -m src.ingest.load`, then "
            "`python -m src.ingest.chunk`, then `python -m src.ingest.embed`.",
        )
    try:
        embed_all(cfg)
    except Exception as exc:  # surface it; never fake numbers
        return False, f"Could not build the Qdrant index: {exc}"
    return True, ""


# ---------------------------------------------------------------------------
# Session state + widget callbacks
# ---------------------------------------------------------------------------

if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None


def _queue_question(question: str) -> None:
    """Send a suggested question immediately."""
    st.session_state.pending_question = question
    st.session_state.question = ""


def _submit_from_input() -> None:
    """Send whatever is in the input bar."""
    question = (st.session_state.get("question") or "").strip()
    if question:
        st.session_state.pending_question = question
        st.session_state.question = ""


def _fill_input(question: str) -> None:
    """Help-centre shortcut: place a question in the composer for editing."""
    st.session_state.question = question


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_user(text: str, index: int) -> None:
    with st.container(key=f"user-{index}"):
        st.markdown(
            f'<div class="mf-user-text">{html_lib.escape(text)}</div>',
            unsafe_allow_html=True,
        )


def _source_pill(url: str, label: str) -> None:
    safe_url = html_lib.escape(url)
    st.markdown(
        f'<div class="mf-source"><span>{html_lib.escape(label)}:</span>'
        f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">{safe_url}</a></div>',
        unsafe_allow_html=True,
    )


def _render_assistant(answer: Answer, index: int) -> None:
    with st.container(key=f"bot-{index}"):
        st.markdown(
            '<div class="mf-bot-label"><span class="mf-dot"></span>HDFC MF Assistant</div>',
            unsafe_allow_html=True,
        )

        if answer.action == "error" or answer.error:
            st.error(answer.text)
            return

        st.markdown(
            f'<div class="mf-answer">{html_lib.escape(answer.text)}</div>',
            unsafe_allow_html=True,
        )
        if answer.source_url:
            label = "Source" if answer.action == "retrieve" else "Official link"
            _source_pill(answer.source_url, label)
        if answer.last_updated:
            st.caption(f"Last updated from sources: {answer.last_updated}")

        # PII refusals must not expose or persist a retrieval trace.
        if answer.action != "pii_refuse" and answer.hits:
            with st.expander("RAG trace (ids, scores, URLs)"):
                st.json(
                    [
                        {
                            "id": hit.get("id"),
                            "score": hit.get("score"),
                            "source_url": hit.get("source_url"),
                            "page_type": hit.get("page_type"),
                            "section": hit.get("section"),
                        }
                        for hit in answer.hits
                    ]
                )


_TYPING_HTML = (
    '<div class="mf-typing"><span></span><span></span><span></span>'
    "<em>Checking official sources…</em></div>"
)

_SUGGESTIONS = [
    ("Minimum SIP?", "What is the minimum SIP for HDFC Mid Cap Fund Direct Growth?"),
    ("Expense ratio?", "What is the expense ratio of HDFC Large Cap Fund Direct Growth?"),
    ("Exit load?", "What is the exit load of HDFC Flexi Cap?"),
    ("Lock-in period?", "What is the lock-in for HDFC ELSS Tax Saver?"),
]

_HELP_FAQS = [
    ("Minimum SIP for HDFC Mid Cap?", "What is the minimum SIP for HDFC Mid Cap Fund Direct Growth?"),
    ("Expense ratio of HDFC Large Cap?", "What is the expense ratio of HDFC Large Cap Fund Direct Growth?"),
    ("Exit load of HDFC Flexi Cap?", "What is the exit load of HDFC Flexi Cap?"),
    ("Lock-in for HDFC ELSS?", "What is the lock-in for HDFC ELSS Tax Saver?"),
    ("Download a capital-gains statement?", "How do I download a capital-gains statement?"),
    ("HDFC AMC fund house details?", "Who is the fund house and when was HDFC AMC incorporated?"),
]

_HELP_CENTRE = "mailto:?subject=HDFC%20MF%20Assistant%20feedback"
_REPORT_WRONG = (
    "mailto:?subject=Report%20a%20wrong%20answer"
    "&body=Question%3A%20%0A%0AWhat%20was%20wrong%3A%20"
)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

st.markdown(
    """
    <div class="mf-header">
      <div class="mf-logo">H</div>
      <div>
        <div class="mf-title">HDFC MF Assistant</div>
        <div class="mf-tagline">Facts-only answers from official sources</div>
      </div>
      <div class="mf-status"><span class="mf-dot"></span>Online</div>
    </div>
    """,
    unsafe_allow_html=True,
)

cfg = _config()
ok, message = _prepare_index()

if not ok:
    st.error(message)
    st.stop()

# Chat is declared before the composer so answers stay above the input even
# though the composer widgets are executed first.
chat_area = st.container()
composer_area = st.container()
help_area = st.container()

pending = st.session_state.pending_question
st.session_state.pending_question = None

with composer_area:
    st.markdown('<div class="mf-chips-label">Suggested questions</div>', unsafe_allow_html=True)
    with st.container(key="chips"):
        chip_columns = st.columns(len(_SUGGESTIONS))
        for i, (label, question) in enumerate(_SUGGESTIONS):
            with chip_columns[i]:
                st.button(
                    label,
                    key=f"chip-{i}",
                    on_click=_queue_question,
                    args=(question,),
                    use_container_width=True,
                )

    with st.container(key="composer"):
        input_column, send_column = st.columns([5, 1], vertical_alignment="bottom")
        with input_column:
            st.text_input(
                "Your question",
                placeholder="Ask about SIP, expense ratio, exit load, lock-in…",
                label_visibility="collapsed",
                key="question",
            )
        with send_column:
            st.button(
                "Send",
                type="primary",
                use_container_width=True,
                on_click=_submit_from_input,
            )

with help_area:
    with st.expander("Help Centre", expanded=False):
        st.markdown('<div class="mf-help-label">Quick questions</div>', unsafe_allow_html=True)
        with st.container(key="help-faqs"):
            faq_columns = st.columns(2)
            for i, (label, question) in enumerate(_HELP_FAQS):
                with faq_columns[i % 2]:
                    st.button(
                        label,
                        key=f"faq-{i}",
                        on_click=_fill_input,
                        args=(question,),
                        use_container_width=True,
                    )

        st.markdown('<div class="mf-help-label">Support</div>', unsafe_allow_html=True)
        st.markdown(
            f"""
            <div class="mf-support">
              <a href="{_HELP_CENTRE}">✉️ Email</a>
              <a href="{html_lib.escape(cfg.links['statements_help'])}" target="_blank" rel="noopener noreferrer">💬 Contact support</a>
              <a href="{_REPORT_WRONG}">🚩 Report a wrong answer</a>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="mf-fineprint">This assistant shares facts only, not investment advice.</div>',
            unsafe_allow_html=True,
        )

with chat_area:
    if not st.session_state.messages and not pending:
        st.markdown(
            f"""
            <div class="mf-empty">
              <div class="mf-empty-icon">✦</div>
              <h3>Ask a factual question</h3>
              <p>{html_lib.escape(cfg.ui["welcome"])}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    for i, message in enumerate(st.session_state.messages):
        if message["role"] == "user":
            _render_user(message["text"], i)
        else:
            _render_assistant(message["answer"], i)

    if pending:
        user_index = len(st.session_state.messages)
        st.session_state.messages.append({"role": "user", "text": pending})
        _render_user(pending, user_index)

        typing_slot = st.empty()
        typing_slot.markdown(_TYPING_HTML, unsafe_allow_html=True)
        answer = ask(pending, cfg)
        typing_slot.empty()

        assistant_index = len(st.session_state.messages)
        st.session_state.messages.append({"role": "assistant", "answer": answer})
        _render_assistant(answer, assistant_index)


with st.sidebar:
    st.markdown('<div class="mf-side-title">About</div>', unsafe_allow_html=True)
    st.write("Facts-only answers from five public Groww pages for HDFC funds.")
    st.write("No investment advice. No returns math. No PII stored.")
    st.caption(cfg.ui["disclaimer"])
    store = "local (embedded)" if cfg.qdrant.get("mode") != "server" else "server"
    st.caption(f"Vector store: Qdrant — {store}")
    st.caption(f"Embeddings: {cfg.embedding['model']}")
