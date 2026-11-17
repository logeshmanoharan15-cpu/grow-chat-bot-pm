# Groww Mutual Fund FAQ Assistant

A facts-only RAG prototype that answers factual questions about five HDFC Mutual Fund
pages on Groww. Every answer is grounded in retrieved page text, carries one source URL,
and refuses advice, return comparisons, and PII.

Two UIs ship with the demo: **Streamlit** (`src/ui/streamlit_app.py`, the deployment
entry point) and a **FastAPI + HTML** app (`src/ui/app.py`, useful as an API).

---

## Scope

Only these five public pages are scraped. Internal links are never followed.

| page_type | Page |
|-----------|------|
| `amc` | [HDFC Mutual Fund](https://groww.in/mutual-funds/amc/hdfc-mutual-funds) |
| `large_cap` | [HDFC Large Cap Fund Direct Growth](https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth) |
| `flexi_cap` | [HDFC Flexi Cap Direct Plan Growth](https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth) |
| `elss` | [HDFC ELSS Tax Saver Fund Direct Plan Growth](https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth) |
| `mid_cap` | [HDFC Mid Cap Fund Direct Growth](https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth) |

Official factsheet / AMFI / SEBI / Groww-help links are used only as citation or
education links. They are **not** scraped.

---

## Stack

| Layer | Choice |
|-------|--------|
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (384-d, normalized) |
| Vector DB | Qdrant (embedded local mode by default), cosine space, collection `mf_faq` |
| Generation | Groq (`GROQ_MODEL` from `.env`) |
| UI | Streamlit (primary) + FastAPI/HTML (API) |
| Tests | pytest (offline, no Groq calls) |

If `GROQ_API_KEY` is blank, generation falls back to a **deterministic extractive
answer** from the retrieved facts chunk. The demo still works; it simply does not
rephrase the fact with an LLM.

### Vector store

`config.yaml -> qdrant.mode` selects how Qdrant is reached:

| mode | Behavior |
|------|----------|
| `local` (default) | Embedded Qdrant persisted at `qdrant.path` (`data/qdrant/`). No server needed. Only one process can hold the folder at a time, so stop the API before re-running `embed`. |
| `server` | Connects to a running Qdrant at `qdrant.url`. Allows ingest and query at the same time. Start one with `docker run --rm -p 6333:6333 qdrant/qdrant`. |

Set `qdrant.api_key` if the server requires auth. Qdrant point ids are deterministic
UUIDv5 values derived from the chunk ids; the original id is kept in the payload
field `chunk_id`, so retrieval traces still show ids like `elss-facts-001`.

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set GROQ_API_KEY (optional for the deterministic fallback)
```

`.env`:

```dotenv
GROQ_API_KEY=
GROQ_MODEL=qwen/qwen3.8-27b
```

> Confirmed at run time: the Groq account exposes `qwen/qwen3.8-27b`, which is
> what `.env.example` uses.

---

## Run the pipeline

Each phase is a separate runnable step and writes an inspectable artifact.

```bash
python -m src.ingest.load      # phase 1: fetch 5 URLs -> data/raw + data/loaded
python -m src.ingest.chunk     # phase 2: section-aware chunks -> data/chunks
python -m src.ingest.embed     # phase 3: embed + upsert -> data/qdrant + manifest
```

Retrieval and generation run through the query pipeline:

```bash
python -m src.query.retrieve --q "ELSS lock-in?"
streamlit run src/ui/streamlit_app.py         # Streamlit UI on http://127.0.0.1:8501
python -m src.ui.app                          # FastAPI UI on http://127.0.0.1:8000
# or: uvicorn src.ui.app:app --host 127.0.0.1 --port 8000
```

Re-running `load -> chunk -> embed` is safe: raw/loaded files are overwritten,
`chunks.jsonl` is rewritten, and the Qdrant collection is deleted and recreated.

---

## Deploy the Streamlit UI

```bash
streamlit run src/ui/streamlit_app.py
```

On Streamlit Community Cloud (or any host), add secrets in the dashboard:

```toml
GROQ_API_KEY = "gsk_..."
GROQ_MODEL = "qwen/qwen3.8-27b"

# Optional: use a hosted Qdrant instead of the embedded folder
QDRANT_MODE = "server"
QDRANT_URL = "https://your-cluster.qdrant.io:6333"
QDRANT_API_KEY = "..."
```

`src/ui/streamlit_app.py` copies those secrets into environment variables before
loading config. All visual styling lives in `src/ui/theme.css` (dark navy theme,
glass chat cards, suggestion chips, Help Centre); edit that one file to tweak the
look. If the Qdrant collection is missing, the app rebuilds the index from
`data/chunks/chunks.jsonl` on first load (the embedding model is downloaded once).
`data/qdrant/` is gitignored, so the local folder is not shipped to the host; set
`QDRANT_MODE=server` for a persistent hosted index.

---

## FastAPI endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Tiny HTML UI: welcome, 3 example chips, disclaimer, ask box, RAG trace |
| `GET` | `/health` | Liveness check |
| `GET` | `/api/meta` | Welcome copy, disclaimer, example questions |
| `POST` | `/api/ask` | `{"question": "..."}` -> answer, source URL, last updated, action, hits |

Example:

```bash
curl -s -X POST http://127.0.0.1:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is the lock-in for HDFC ELSS Tax Saver?"}'
```

```json
{
  "text": "Lock-in: 3 years.",
  "source_url": "https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth",
  "last_updated": "2026-11-10T17:59:46.630304+00:00",
  "action": "retrieve",
  "hits": [ ... ],
  "disclaimer": "Facts-only. No investment advice."
}
```

`action` is one of `retrieve`, `refuse`, `pii_refuse`, `factsheet`,
`out_of_scope`, `error`.

---

## RAG stages and artifacts

| Stage | Path | Contents |
|-------|------|----------|
| Load | `data/raw/*.html` | Raw HTML for the 5 allowlisted URLs |
| Load | `data/loaded/*.json` | Title, URL, `fetched_at`, clean text, structured fields |
| Chunk | `data/chunks/chunks.jsonl` | One facts chunk + section-aware body chunks per page |
| Embed | `data/qdrant/` | Persistent Qdrant collection |
| Embed | `data/embeddings/manifest.json` | Model, dim, collection, count, ids |
| Retrieve | `data/retrieval/*.json` | Query, ids, scores, URLs, previews |

Extraction prefers Groww's embedded `__NEXT_DATA__` JSON (reliable expense ratio,
exit load, min SIP, lock-in, benchmark) and falls back to text regexes if the
layout changes. Missing fields are `null`; the chunk simply omits that line.

---

## Safety policy (runs before retrieval and Groq)

| Signal | Action |
|--------|--------|
| PAN, Aadhaar, phone, email, OTP, folio/account numbers | `pii_refuse`; no retrieval file, no Groq call, no logging |
| Buy/sell, "should I", best fund, portfolio, allocation | `refuse` + AMFI investor-education link |
| Returns, CAGR, outperformance, "which performed better" | `factsheet` redirect; never compute or compare |
| Scheme/AMC outside the five pages | `out_of_scope` + education link |
| Weak retrieval (score below `retrieval.min_score`) | "not in the ingested pages"; never invent numbers |
| LLM cites a URL outside retrieved hits | replaced with the top hit's `source_url` |
| LLM returns extra sentences | first 3 sentences kept |

---

## Configuration

`config.yaml` controls everything changeable without code edits:

- `sources[]` — the five allowlisted URLs
- `chunk.strategy`, `chunk.size`, `chunk.overlap`
- `embedding.model`
- `qdrant.mode`, `qdrant.path`, `qdrant.url`, `qdrant.collection`
- `retrieval.top_k`, `retrieval.min_score`
- `links.factsheet`, `links.education`, `links.statements_help`
- `ui.welcome`, `ui.examples[]`, `ui.disclaimer`
- `scheme_aliases`, `scope.out_of_scope_aliases`

Changing `embedding.model` or `qdrant.collection` requires re-running
`python -m src.ingest.embed`. Retrieval detects the mismatch and asks you to
re-embed.

---

## Tests

```bash
python -m pytest -q
# 36 passed
```

`tests/test_safety.py` covers the P1–P6, A1–A6, R1–R4, F1–F6 matrix.
`tests/test_edge_cases.py` covers extraction/chunk/citation behavior.
`tests/test_streamlit_ui.py` runs the deployed Streamlit script through
Streamlit's `AppTest` harness (factual answer, advice refusal, PII refusal,
no trace on PII).
`tests/test_phase8_edge_cases.py` covers the Phase 8 table: HTTP failure,
missing fields, layout change, out-of-scope schemes (other AMCs and unlisted
HDFC schemes), ambiguity, empty Qdrant, model change, Groq failure, weak
retrieval, hallucinated citation, sentence cap, PII non-persistence, statement
help, and AMC facts.

---

## Known limits

- Corpus is five Groww public pages only; other AMCs and unlisted HDFC schemes are
  out of scope.
- Groww layout changes can break extraction. Structured JSON is preferred; regex
  fallbacks may return `null` fields.
- Numbers reflect `fetched_at`, not live NAV. Re-run ingest to refresh.
- The assistant cannot download a user's statement or access holdings; it only
  points to the public Groww help flow.
- Retrieval quality is bounded by MiniLM and chunk quality. Ambiguous field
  questions ask which of the four schemes is meant.
- With no `GROQ_API_KEY`, answers are extractive rather than LLM-rephrased.
- No chat log is written to disk. In-session UI state only.
