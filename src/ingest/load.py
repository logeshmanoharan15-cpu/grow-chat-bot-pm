from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from src.config import Config, load_config

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Fields we try to extract for every page. Missing values stay None; we never invent.
FIELD_KEYS = [
    "expense_ratio",
    "exit_load",
    "min_sip",
    "lock_in",
    "riskometer",
    "benchmark",
    "fund_house",
    "category",
    "amc_incorporation_date",
    "sponsor",
    "trustee",
    "managing_director",
    "total_aum",
]

# Regex fallbacks used when Groww's embedded JSON is unavailable (layout change)
# or when extract_fields is called with plain text in tests.
FIELD_PATTERNS: dict[str, list[str]] = {
    "expense_ratio": [
        r"expense ratio[^\d%]{0,80}(\d+\.\d+\s*%|\d+\s*%)",
    ],
    "min_sip": [
        r"min\.?\s*for\s*sip\s*[₹rs.\s]*([\d,]+)",
        r"minimum sip[^\d₹]{0,40}[₹rs.\s]*([\d,]+)",
    ],
    "exit_load": [
        r"(exit load of [^\n.]{5,120})",
        r"exit load\s*\n\s*(nil|none|[^\n]{3,80})",
    ],
    "lock_in": [
        r"lock[- ]?in\s*(?:period)?\s*[:\-]?\s*([^\n.]{2,80})",
        r"(3\s*years?\s*lock[- ]?in)",
    ],
    "riskometer": [
        r"is rated\s+([^\n.]{3,40}?)\s+risk",
        r"\b(very high risk|high risk|moderately high risk|moderate risk|low to moderate risk|low risk|very low risk)\b",
    ],
    "benchmark": [
        r"fund benchmark[:\s]+([^\n]+?)(?:scheme information|\.|$)",
        r"benchmark[:\s]+((?:NIFTY|Nifty|BSE|S&P)[^\n]{3,80})",
    ],
}


def _next_data(html: str) -> dict:
    """Return Groww's embedded __NEXT_DATA__ JSON when present."""
    try:
        soup = BeautifulSoup(html, "lxml")
        tag = soup.find("script", id="__NEXT_DATA__")
        if tag is None or not tag.string:
            return {}
        return json.loads(tag.string)
    except Exception:
        return {}


def _page_props(html: str) -> dict:
    return ((_next_data(html).get("props") or {}).get("pageProps") or {})


def _clean_value(value) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip(" \t\r\n;")
    return text or None


def _format_money(value) -> str | None:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount.is_integer():
        return f"₹{int(amount):,}"
    return f"₹{amount:,.2f}"


def _format_date(value) -> str | None:
    text = _clean_value(value)
    if not text:
        return None
    # 1999-12-10T00:00 -> 10 Dec 1999
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return dt.strftime("%d %b %Y")
        except ValueError:
            return text
    return text


def _format_lock_in(lock) -> str | None:
    if not isinstance(lock, dict):
        return _clean_value(lock)
    parts = []
    for unit in ("years", "months", "days"):
        value = lock.get(unit)
        if value:
            parts.append(f"{int(value)} {unit}")
    return " ".join(parts) or None


def _find_amc_entry(props: dict) -> dict | None:
    entries = props.get("amcMainPageData") or []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("search_id") == "hdfc-mutual-funds":
            return entry
    return None


def _structured_fields(html: str, page_type: str | None) -> dict[str, str | None]:
    """Prefer Groww's embedded structured JSON over fragile text regexes."""
    props = _page_props(html)
    fields: dict[str, str | None] = {}

    if page_type == "amc":
        entry = _find_amc_entry(props)
        if not entry:
            return fields
        info = entry.get("key_information") or {}
        total_aum = info.get("total_aum")
        try:
            total_aum_text = f"₹{float(total_aum):,.2f} Cr" if total_aum else None
        except (TypeError, ValueError):
            total_aum_text = _clean_value(total_aum)
        sponsor = info.get("sponsor_name") or []
        fields.update(
            {
                "fund_house": _clean_value(info.get("fund_house")),
                "amc_incorporation_date": _format_date(info.get("amc_incorporation_date")),
                "sponsor": ", ".join(str(s) for s in sponsor) or None,
                "trustee": _clean_value(info.get("trustee_organisation")),
                "managing_director": _clean_value(info.get("md")),
                "total_aum": total_aum_text,
            }
        )
        return {k: v for k, v in fields.items() if v}

    ssd = props.get("mfServerSideData") or {}
    if not ssd:
        return fields

    expense = _clean_value(ssd.get("expense_ratio"))
    if expense and "%" not in expense:
        expense = f"{expense}%"

    category = _clean_value(ssd.get("category"))
    sub_category = _clean_value(ssd.get("sub_category"))
    if category and sub_category:
        category = f"{category} / {sub_category}"

    fields.update(
        {
            "expense_ratio": expense,
            "exit_load": _clean_value(ssd.get("exit_load")),
            "min_sip": _format_money(ssd.get("min_sip_investment")),
            "lock_in": _format_lock_in(ssd.get("lock_in")),
            "benchmark": _clean_value(ssd.get("benchmark")),
            "fund_house": _clean_value(ssd.get("fund_house")),
            "category": category,
        }
    )
    return {k: v for k, v in fields.items() if v}


def _clean_text(html: str, page_type: str | None = None, title: str = "") -> str:
    """Extract readable text, preferring the clean body sections Groww renders."""
    if page_type == "amc":
        amc_text = _amc_text(html)
        if amc_text:
            return amc_text

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "noscript", "header"]):
        tag.decompose()
    root = soup.find("main") or soup.body or soup
    text = root.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Scheme pages: the "About" block is the clean, fact-bearing prose. The
    # preceding blocks are holdings/returns tables and lists of other funds.
    marker = f"About\n{title}" if title else ""
    start = text.find(marker) if marker else -1
    if start == -1:
        start = text.find("\nAbout\n")
        if start != -1:
            start += 1
    if start != -1:
        text = text[start:]

    for end_marker in ("\nHome\n>", "\nMutual Funds\n>"):
        end = text.find(end_marker)
        if end != -1:
            text = text[:end]
            break
    return text.strip()


def _amc_text(html: str) -> str:
    entry = _find_amc_entry(_page_props(html))
    if not entry:
        return ""
    parts: list[str] = []
    for section in entry.get("contentList") or []:
        section_title = _clean_value(section.get("title")) or ""
        body = BeautifulSoup(section.get("content") or "", "lxml").get_text("\n", strip=True)
        if body:
            parts.append(f"{section_title}\n{body}" if section_title else body)

    info = entry.get("key_information") or {}
    labels = [
        ("Fund house", "fund_house"),
        ("Asset management company", "asset_management_company"),
        ("Date of incorporation", "amc_incorporation_date"),
        ("Sponsor", "sponsor_name"),
        ("Trustee", "trustee_organisation"),
        ("Managing director", "md"),
        ("Chief executive officer", "ceo"),
        ("Chief investment officer", "cio"),
        ("Compliance officer", "compliance_officer"),
        ("Investor service officer", "investor_service_officer"),
        ("Total AUM", "total_aum"),
        ("Address", "address"),
    ]
    lines = []
    for label, key in labels:
        value = info.get(key)
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        value = _clean_value(value)
        if value:
            lines.append(f"{label}: {value}")
    if lines:
        parts.append("Key information\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _trim_chrome(text: str, title: str) -> str:
    starts = []
    for marker in ("Min. for SIP", "Expense ratio", title or ""):
        if marker:
            i = text.find(marker)
            if i != -1:
                starts.append(i)
    if starts:
        text = text[min(starts) :]
    for marker in ("Vaishnavi Tech Park", "© 2016-", "Download the App"):
        end = text.find(marker)
        if end != -1:
            text = text[:end]
            break
    return text.strip()


def _first_match(text: str, patterns: list[str]) -> str | None:
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            value = (m.group(1) if m.lastindex else m.group(0)).strip()
            value = re.sub(r"\s+", " ", value).strip(" :-")
            if not value:
                continue
            if value.lower().startswith("a fee payable"):
                continue
            return value
    return None


def extract_fields(
    text: str, page_type: str | None = None, html: str | None = None
) -> dict[str, str | None]:
    """Extract FAQ fields. Structured JSON first, regex only as fallback."""
    fields: dict[str, str | None] = {key: None for key in FIELD_KEYS}

    if html:
        structured = _structured_fields(html, page_type)
        fields.update({k: structured.get(k) for k in FIELD_KEYS})

    if page_type == "amc":
        # AMC overview pages do not carry scheme FAQ fields.
        if html:
            return fields
        return {key: None for key in FIELD_KEYS}

    if not html:
        for key, patterns in FIELD_PATTERNS.items():
            fields[key] = _first_match(text, patterns)
        if fields.get("min_sip") and not str(fields["min_sip"]).startswith("₹"):
            fields["min_sip"] = f"₹{fields['min_sip']}"

    if not fields.get("riskometer"):
        fields["riskometer"] = _first_match(text, FIELD_PATTERNS["riskometer"])
    if fields.get("riskometer") and not fields["riskometer"].lower().endswith("risk"):
        fields["riskometer"] = f"{fields['riskometer']} risk"

    # Text regex can be more precise for exit load than a raw JSON value.
    if not fields.get("exit_load"):
        fields["exit_load"] = _first_match(text, FIELD_PATTERNS["exit_load"])

    return fields


def fetch_html(url: str, timeout: float = 30.0) -> tuple[int, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
        resp = client.get(url)
        return resp.status_code, resp.text


def load_all(cfg: Config | None = None) -> list[dict]:
    cfg = cfg or load_config()
    allowed = cfg.source_urls()
    raw_dir: Path = cfg.paths["raw_dir"]
    loaded_dir: Path = cfg.paths["loaded_dir"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    loaded_dir.mkdir(parents=True, exist_ok=True)

    documents = []
    errors = []
    for i, source in enumerate(cfg.sources):
        if source.url not in allowed:
            raise SystemExit(f"URL not allowlisted: {source.url}")
        status, html = fetch_html(source.url)
        if status >= 400:
            errors.append(f"{source.page_type} HTTP {status}")
            continue
        fetched_at = datetime.now(timezone.utc).isoformat()
        (raw_dir / f"{source.page_type}.html").write_text(html, encoding="utf-8")
        text = _clean_text(html, source.page_type, source.title)
        fields = extract_fields(text, source.page_type, html=html)
        doc = {
            "page_type": source.page_type,
            "title": source.title,
            "source_url": source.url,
            "fetched_at": fetched_at,
            "http_status": status,
            "text": text,
            "fields": fields,
        }
        (loaded_dir / f"{source.page_type}.json").write_text(
            json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        documents.append(doc)
        if i < len(cfg.sources) - 1:
            time.sleep(0.8)

    index = [
        {
            "page_type": d["page_type"],
            "source_url": d["source_url"],
            "fetched_at": d["fetched_at"],
            "field_keys": [k for k, v in d["fields"].items() if v],
        }
        for d in documents
    ]
    (loaded_dir / "_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")

    if errors:
        raise SystemExit("Ingest failed (do not skip pages): " + "; ".join(errors))
    if len(documents) != 5:
        raise SystemExit(f"Expected 5 pages, got {len(documents)}")
    return documents


def main() -> None:
    docs = load_all()
    print(f"Loaded {len(docs)} pages into data/loaded/")
    for d in docs:
        keys = [k for k, v in d["fields"].items() if v]
        print(f"  {d['page_type']}: fields={keys or 'none (text only)'}")


if __name__ == "__main__":
    sys.exit(main())
