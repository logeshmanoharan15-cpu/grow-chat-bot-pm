# Implementation

**Source:** [architecture.md](architecture.md) · [PRD.md](PRD.md)

Build in this order. Save each stage’s output on disk so the RAG demo can be shown.

---

## 1. Creating directory

```
.
├── config.yaml
├── .env / .env.example          # GROQ_API_KEY, GROQ_MODEL
├── requirements.txt
├── prompts/system.txt
├── prompts/refuse.txt
├── src/
│   ├── config.py
│   ├── ingest/load.py           # scrape
│   ├── ingest/chunk.py
│   ├── ingest/embed.py          # embed + Qdrant upsert
│   ├── query/safety.py
│   ├── query/retrieve.py
│   ├── query/generate.py
│   ├── query/pipeline.py
│   └── ui/
│       ├── streamlit_app.py     # Streamlit UI (deployment entry point)
│       └── app.py               # FastAPI + HTML API/UI
└── data/
    ├── raw/                     # HTML
    ├── loaded/                  # extracted JSON
    ├── chunks/chunks.jsonl
    ├── embeddings/manifest.json
    ├── qdrant/                  # vector DB
    └── retrieval/               # query traces
```

`config.yaml` holds the 5 URLs, chunk size/overlap, embedding model `sentence-transformers/all-MiniLM-L6-v2`, Qdrant mode/path/url/collection, `top_k`, UI copy, and official links (factsheet, AMFI education, statements help).

`.env` holds Groq key and model (`qwen/qwen3.8-27b` — confirm Groq id at run time).

**Run:** `python -m src.ingest.load` → `chunk` → `embed`, then `streamlit run src/ui/app.py`.

---

## 2. Scraping document

**File:** `src/ingest/load.py`  
**Only these 5 URLs.** Do not follow links on the page.

| page_type | URL |
|-----------|-----|
| amc | https://groww.in/mutual-funds/amc/hdfc-mutual-funds |
| large_cap | https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth |
| flexi_cap | https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth |
| elss | https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth |
| mid_cap | https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth |

Steps:

1. GET each URL (httpx + User-Agent). Abort if URL is not in the config allowlist.
2. Save HTML → `data/raw/{page_type}.html`.
3. Parse with BeautifulSoup: strip script/nav/footer; keep main text.
4. Pull FAQ fields when present: expense ratio, exit load, min SIP, lock-in, riskometer, benchmark. Missing → `null`, never invent.
5. Save `data/loaded/{page_type}.json` with `title`, `source_url`, `fetched_at` (UTC), `text`, `fields`.

`fetched_at` is the **Last updated from sources** stamp.

---

## 3. Chunking

**File:** `src/ingest/chunk.py`  
**Strategy:** section-aware (config can switch to simple sliding window).

Keep the scheme **name and its facts in the same chunk**.

For each loaded JSON:

1. **Facts chunk** — if any fields exist, one chunk like:  
   `Title: … | Expense ratio: … | Exit load: … | Min SIP: … | Lock-in: …`  
   id: `{page_type}-facts-001`
2. **Body chunks** — split remaining text on headings / paragraphs; cap at `chunk.size` with `chunk.overlap`; prefix every chunk with `Title: {title}`.

Write `data/chunks/chunks.jsonl`:

```json
{"id": "elss-facts-001", "text": "...", "metadata": {"source_url": "...", "page_type": "elss", "title": "...", "fetched_at": "...", "section": "facts"}}
```

Overwrite the file each run so old chunks do not linger.

---

## 4. Embedding

**File:** shared MiniLM helper used by ingest and query.

- Model from config: `sentence-transformers/all-MiniLM-L6-v2`
- `encode(..., normalize_embeddings=True)` (384-dim)
- Same model name for ingest and query — never two models

Write `data/embeddings/manifest.json`: model, dim, collection, count, ids. Do not dump full vectors.

---

## 5. Storing data in vector DB

**DB:** Qdrant at `config.qdrant.path` (embedded local mode) or `config.qdrant.url` (server mode)  
**Collection:** `config.qdrant.collection` (e.g. `mf_faq`)  
**Space:** cosine (matches normalized embeddings)

```
upsert(ids, embeddings, documents=chunk.text, metadatas=chunk.metadata)
```

On full re-ingest, delete collection then recreate so dropped chunks disappear.

**File:** `src/ingest/embed.py` — read jsonl → embed → upsert → write manifest.

---

## 6. Retrieval logic

**Files:** `safety.py` → `retrieve.py` → `generate.py` → `pipeline.py`

```
question
  → guardrails (PII / advice / returns)     # may stop here
  → embed query with same MiniLM
  → Qdrant top_k (same collection)
  → save data/retrieval/{timestamp}.json
  → Groq with context + system prompt
  → answer: ≤3 sentences + one source URL + last updated
```

**Retrieve:** optional `page_type` filter if the question names a scheme; otherwise no filter. Persist ids, scores, `source_url`, short preview.

**Generate:** Groq model from `.env`. Ground only on hits. If the model cites a URL not in hits, replace with the top hit’s `source_url`. If no evidence: say it is not in the five pages. “How to download statement” may use `config.links.statements_help` if the corpus has no how-to.

**Guardrails (before retrieve / Groq):**

| Signal | Action |
|--------|--------|
| PAN, Aadhaar, phone, email, OTP, account numbers | Refuse. Do not log, save, or send to Groq |
| Buy/sell, “should I”, best fund, portfolio | `prompts/refuse.txt` + education link |
| Returns, CAGR, which performed better | No math. Factsheet link |

---

## 7. UI

**File:** `src/ui/streamlit_app.py` + `src/ui/theme.css`
(Streamlit; FastAPI equivalent in `src/ui/app.py`)

- Dark navy header: title, tagline, online indicator
- Chat transcript: user questions as right-aligned blue bubbles, answers as
  left-aligned glass cards with citation pill and last-updated caption
- Suggested question chips above the composer; glowing **Send** button
- Typing indicator while the pipeline runs; answers fade in
- Help Centre bar at the bottom: quick questions fill the composer, plus email,
  support link, report-a-wrong-answer, and facts-only disclaimer
- Expander: last retrieval ids/scores/URLs (hide on PII refuse)
- No chat log on disk; conversation lives in Streamlit session state only
- All visual styling is in `src/ui/theme.css`
- Responsive layouts for desktop and mobile
- Deployment: reads Streamlit secrets (`GROQ_API_KEY`, optional `QDRANT_*`) and
  rebuilds a missing Qdrant index from `data/chunks/chunks.jsonl` on first load

---

## 8. Edge cases

| Case | Expected |
|------|----------|
| HTTP 4xx/5xx on a source | Fail ingest; do not silently skip a page |
| Field missing on page | `null` in JSON; chunk omits that line |
| Groww HTML layout change | Extractor may return empty `fields`; still chunk `text` |
| Question about a scheme not in the 5 pages | “Not in ingested pages” + education link |
| Ambiguous “expense ratio” (no scheme name) | Retrieve all; answer only if one scheme is clearly in context, else ask which of the four |
| Empty Qdrant / forgot embed | Query path errors with “run ingest first” |
| Groq down / bad model id | Show error; do not fake numbers |
| User pastes PII inside a factual question | PII wins: refuse, no retrieval file |
| Advice + fact in one message (“expense ratio, should I buy?”) | Refuse advice path (safer) |
| Performance question | Factsheet only; never compute or compare |
| Weak retrieval (low scores) | Do not invent; say data is not in the pages |
| Citation URL hallucinated by LLM | Overwrite with retrieved `source_url` |
| Extra sentences from LLM | Keep first 3 |
| Re-run scrape | Overwrite raw/loaded; then re-chunk and re-embed |
| Changing embedding model in config | Must re-run embed |

---

## 9. Test cases for guardrails

Use small unit tests on `safety.classify` (no network). Templates must not retrieve.

### PII — must refuse, no retrieve, no persist

| ID | Input | Expect |
|----|--------|--------|
| P1 | `My PAN is ABCDE1234F, what is ELSS lock-in?` | `pii_refuse` |
| P2 | `Aadhaar 1234 5678 9012` | `pii_refuse` |
| P3 | `Email me at user@gmail.com` | `pii_refuse` |
| P4 | `Call 9876543210` | `pii_refuse` |
| P5 | `OTP 123456` | `pii_refuse` |
| P6 | `My folio / account 123456789012` | `pii_refuse` |

### Advice — refuse + education link, no retrieve

| ID | Input | Expect |
|----|--------|--------|
| A1 | `Should I buy HDFC Large Cap?` | `refuse` + education URL |
| A2 | `Should I sell my ELSS?` | `refuse` |
| A3 | `Which is the best fund?` | `refuse` |
| A4 | `Recommend a portfolio` | `refuse` |
| A5 | `How much should I allocate to mid cap?` | `refuse` |
| A6 | `Expense ratio of large cap, should I buy it?` | `refuse` (advice wins) |

### Performance — no compute, factsheet link

| ID | Input | Expect |
|----|--------|--------|
| R1 | `What is the 3 year return of Flexi Cap?` | `factsheet` |
| R2 | `Which fund performed better, large cap or mid cap?` | `factsheet` |
| R3 | `CAGR of HDFC ELSS` | `factsheet` |
| R4 | `Did large cap outperform Nifty?` | `factsheet` |

### Allow through (factual)

| ID | Input | Expect |
|----|--------|--------|
| F1 | `Expense ratio of HDFC Large Cap Fund Direct Growth?` | `retrieve` |
| F2 | `ELSS lock-in?` | `retrieve` |
| F3 | `Minimum SIP for HDFC Mid Cap?` | `retrieve` |
| F4 | `Exit load of Flexi Cap?` | `retrieve` |
| F5 | `Riskometer / benchmark of large cap?` | `retrieve` |
| F6 | `How to download capital-gains statement?` | `retrieve` (or statements help citation) |

### Negative checks after classify

- For P\*, A\*, R\*: `data/retrieval/` has **no new file**; Groq is **not** called.
- For F\*: Groq is called only with retrieved context; answer has exactly one URL from hits (or configured statements/factsheet/education link per policy).
- Answer body ≤ 3 sentences; includes `Last updated from sources`.
- No PII substring appears in logs or retrieval JSON.
