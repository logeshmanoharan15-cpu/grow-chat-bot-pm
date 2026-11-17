# Product Requirements Document

**Product:** Groww Mutual Fund FAQ Assistant  
**Type:** Working prototype (facts-only RAG chatbot)  
**Source:** Problem statement (Groww app assignment)  
**Version:** 1.0  
**Date:** 2026-11-01

---

## 1. Overview

Build a small FAQ assistant on Groww public pages that answers **factual** questions about selected HDFC Mutual Fund schemes (expense ratio, exit load, minimum SIP, ELSS lock-in, riskometer, benchmark, how to download statements).

Every answer must include **one source link**. The product must **not** give investment advice.

This is a **RAG demo**. Each pipeline stage must persist data so it can be inspected (load → chunk → embed → retrieve → generate).

---

## 2. Problem and users

Retail users comparing schemes ask the same factual questions. Support and content teams repeat those answers.

The assistant should:

- Answer scheme facts from official public pages only.
- Cite a single source URL on every answer.
- Refuse opinion, buy/sell, and portfolio questions politely, with a facts-only message and a relevant educational link.

---

## 3. Goals and non-goals

### Goals

- Facts-only Q&A for the listed AMC page and four schemes.
- Visible RAG stages with stored intermediate artifacts.
- Configurable ingestion/retrieval/LLM settings (config + `.env`).
- Tiny UI: welcome line, 3 example questions, disclaimer.

### Non-goals

- Investment advice, recommendations, or portfolio construction.
- Performance computation or return comparison.
- Scraping pages beyond the 5 listed URLs (no follow of internal links).
- Collecting or storing PII (PAN, Aadhaar, account numbers, OTPs, emails, phones).
- Using app back-end screenshots or third-party blogs as sources.

---

## 4. In-scope data sources

Scrape **only these 5 pages**. Do not crawl or scrape links internal to them.

| # | Page | URL |
|---|------|-----|
| 1 | AMC overview — HDFC Mutual Fund | https://groww.in/mutual-funds/amc/hdfc-mutual-funds |
| 2 | Large Cap — HDFC Large Cap Fund Direct Growth | https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth |
| 3 | Flexi Cap — HDFC Flexi Cap Direct Plan Growth | https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth |
| 4 | ELSS (tax saver) — HDFC ELSS Tax Saver Fund Direct Plan Growth | https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth |
| 5 | Mid Cap — HDFC Mid Cap Fund Direct Growth | https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth |

**Public sources only.** Factsheets and SEBI/AMFI educational pages may be used as **citation/education links** when answering (e.g. performance asked → link official factsheet; advice refused → educational link). They are not additional scrape targets unless later expanded in config.

---

## 5. Product requirements

### 5.1 FAQ assistant behavior

The prototype must answer factual queries such as:

- Expense ratio of a listed scheme
- ELSS lock-in
- Minimum SIP
- Exit load
- Riskometer / benchmark
- How to download a capital-gains statement

Rules:

1. Identify the exact fact asked; answer only if it is in retrieved official content.
2. Show **one clear citation link** in every answer.
3. Keep answers **≤ 3 sentences**.
4. Include **Last updated from sources: &lt;timestamp/date&gt;**.
5. Refuse opinionated/portfolio questions (e.g. “Should I buy/sell?”) with a polite facts-only message plus a relevant educational link.
6. If asked about returns/performance: **do not compute or compare**; link to the official factsheet.

### 5.2 UI (minimum)

- Welcome line.
- **3 example questions**.
- Persistent note: **“Facts-only. No investment advice.”**
- Chat input for user questions.
- Answer area showing text, citation URL, and last-updated line.

### 5.3 Privacy and safety

- Do not accept or store PAN, Aadhaar, account numbers, OTPs, emails, or phone numbers.
- If the user pastes PII, refuse and ask them to remove it; do not persist it.
- No performance claims or ranking of schemes.

---

## 6. Architecture: phase-wise RAG

Implement in phases. **Persist each stage’s output** so the demo can show the pipeline.

### Phase 1 — Data loading

- Fetch HTML for the 5 URLs only.
- Extract readable text and structured fields useful for FAQ (scheme name, expense ratio, exit load, min SIP, lock-in, riskometer, benchmark, AMC overview facts).
- Store raw HTML and extracted documents (e.g. `data/raw/`, `data/loaded/`).
- Record fetch timestamp per URL.

### Phase 2 — Data chunking

- Choose a chunking strategy that fits this corpus (small, mostly tabular/FAQ-like scheme pages).
- Recommended default (agent-chosen, overridable in config):
  - Split by **semantic sections** (scheme facts, AMC overview, statement/help text).
  - Keep related key-value facts in the **same chunk** so “expense ratio” is not split from the scheme name.
  - Modest overlap so headings stay attached to values.
  - Metadata on every chunk: `source_url`, `page_type` (amc | large_cap | flexi_cap | elss | mid_cap), `title`, `fetched_at`.
- Store chunks (e.g. `data/chunks/`).

### Phase 3 — Data embedding

- Embedding model: **`sentence-transformers/all-MiniLM-L6-v2`**.
- Vector DB: **Qdrant** (embedded local mode by default, server mode configurable).
- Store embeddings + metadata + ids.
- Model name, storage location, and collection name must live in **config** so they can be changed without code edits.

### Phase 4 — Data retrieval

- Use the **same embedding model** as ingestion.
- Use the **same Qdrant collection**.
- Retrieve top-k chunks (k in config) for the query.
- Persist retrieval traces for the demo (query, retrieved ids, scores, source URLs).

### Phase 5 — LLM generation

- Groq API; model from `.env` (see §7).
- Instruction style: concise, facts-only, polite refusals, explicit citation.
- Ground answers in retrieved chunks only; if evidence is missing, say so and still provide an educational/official link when possible.
- Output format: ≤3 sentences + one citation URL + last-updated line.

---

## 7. Configuration

### Config file (changeable without code edits)

At minimum:

| Key | Purpose | Default / notes |
|-----|---------|-----------------|
| Source URL list | The 5 pages | Listed in §4 |
| Chunk size / overlap / strategy | Chunking | Section-aware; overridable |
| Embedding model | Ingestion + retrieval | `sentence-transformers/all-MiniLM-L6-v2` |
| Vector DB | Store | Qdrant |
| Qdrant location + collection | Demo inspectability | Embedded local persist or server |
| Top-k | Retrieval | Configurable |
| LLM provider | Generation | Groq (primary) |

### Environment (`.env`, not committed)

- Groq API key.
- LLM model name: **`qwen/qwen3.8-27b`** (read from `.env`; confirm exact Groq model id at implementation time).
- Optional later providers (do not block v1): Gemini, OpenAI, Anthropic, Mistral.

---

## 8. Prompting and refusal policy (skills W1–W3)

| Skill | Requirement |
|-------|-------------|
| W1 — Thinking like a model | Identify the exact fact; decide **answer vs refuse**. |
| W2 — LLMs & prompting | Instruction style; concise phrasing; polite safe-refusals; citation wording. |
| W3 — RAG only | Small-corpus retrieval with accurate citations from AMC / official public pages (Groww listed pages; SEBI/AMFI/factsheet links for education or performance redirect). |

**Refuse** (no scheme pick, no buy/sell, no “best fund”):

- Return a short polite message that the assistant is facts-only.
- Include one relevant educational official link.
- Do not invent numbers.

**Answer** only when a retrieved chunk supports the fact; cite that chunk’s `source_url`.

---

## 9. Deliverables

| Deliverable | Requirement |
|-------------|-------------|
| Working prototype | App (preferred) or notebook; if hosting is not possible, ≤3-min demo video |
| Source list | CSV or MD of **15–25 URLs** used (the 5 scrape targets plus official citation/education URLs such as factsheet, AMFI, SEBI, Groww help for statements) |
| README | Setup steps, scope (AMC + schemes), known limits |
| Sample Q&A | 5–10 queries with assistant answers + links |
| Disclaimer snippet | Facts-only, no advice (same text as UI) |
| Stage artifacts | Explicit stored outputs for load, chunk, embed, retrieve (RAG demo) |

---

## 10. Success criteria

- All 5 pages ingested; no extra crawled URLs.
- Sample factual questions return correct values from those pages, ≤3 sentences, one citation, last-updated line.
- Advice/performance-comparison questions are refused or redirected per policy.
- Pipeline artifacts for each RAG stage are inspectable.
- Embedding model and Qdrant settings are config-driven; Groq model is `.env`-driven.
- UI shows welcome, 3 examples, and “Facts-only. No investment advice.”
- No PII stored.

---

## 11. Known limits (document in README)

- Corpus is five Groww public pages only; answers cannot cover other AMCs or unlisted schemes.
- Groww page layout may change; scrape may break.
- Numbers can go stale; last-updated must reflect fetch time, not “live market.”
- No live account actions (cannot actually download a user’s statement; can only explain the public how-to).
- Retrieval quality is bounded by chunk quality and MiniLM embeddings.

---

## 12. Implementation notes

- Prefer a small local app (e.g. Streamlit/FastAPI + simple HTML) over a notebook if time allows, so UI requirements are met.
- Keep scrape, chunk, embed, and query as separate runnable steps.
- Never log user PII.
- Default disclaimer copy: **Facts-only. No investment advice.**
