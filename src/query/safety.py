from __future__ import annotations

import re
from dataclasses import dataclass

from src.config import ROOT, Config, load_config

PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", re.I)
AADHAAR_RE = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
EMAIL_RE = re.compile(r"\b\S+@\S+\.\S+\b")
PHONE_RE = re.compile(r"\b[6-9]\d{9}\b")
OTP_RE = re.compile(r"\botp\b.{0,12}\b\d{4,8}\b|\b\d{4,8}\b.{0,12}\botp\b", re.I)
ACCOUNT_RE = re.compile(
    r"\b(folio|account|a/c|acct)\b.{0,20}\b\d{8,}\b|\b\d{8,}\b.{0,20}\b(folio|account|a/c)\b",
    re.I,
)

ADVICE_RE = re.compile(
    r"\b(should i|should we|best fund|recommend|portfolio|allocate|"
    r"which is better for me|sip or lumpsum for me)\b",
    re.I,
)
PERFORMANCE_RE = re.compile(
    r"\b(returns?|cagr|outperform(?:ed)?|nav performance|which performed|"
    r"performed better|historic returns|annualised)\b"
    r"|\b(\d+\s*year|\d+y)\s+(return|cagr|annualised)\b",
    re.I,
)
STATEMENT_RE = re.compile(
    r"\b(capital[- ]gains|statement|download.*(statement|capital))\b",
    re.I,
)


@dataclass
class SafetyResult:
    action: str
    message: str = ""
    source_url: str = ""


def classify(question: str, cfg: Config | None = None) -> SafetyResult:
    cfg = cfg or load_config()
    q = question or ""

    if (
        PAN_RE.search(q)
        or AADHAAR_RE.search(q)
        or EMAIL_RE.search(q)
        or PHONE_RE.search(q)
        or OTP_RE.search(q)
        or ACCOUNT_RE.search(q)
    ):
        return SafetyResult(
            action="pii_refuse",
            message=(
                "Please remove personal details such as PAN, Aadhaar, phone, email, OTP, "
                "or account numbers. This assistant does not need them and will not store them."
            ),
        )

    if ADVICE_RE.search(q):
        return SafetyResult(
            action="refuse",
            message=_refuse_text().strip(),
            source_url=cfg.links["education"],
        )

    if PERFORMANCE_RE.search(q):
        return SafetyResult(
            action="factsheet",
            message=(
                "I do not compute or compare returns. "
                "See the official fund documents / factsheet for performance figures."
            ),
            source_url=cfg.links["factsheet"],
        )

    return SafetyResult(action="retrieve")


def is_statement_question(question: str) -> bool:
    return bool(STATEMENT_RE.search(question or ""))


def detect_out_of_scope(question: str, cfg: Config | None = None) -> str | None:
    """Return a named out-of-scope scheme/AMC.

    Out-of-scope names win over generic in-scope aliases, so a question that
    mixes a listed scheme with an unlisted one is refused rather than answered
    from the wrong page.
    """
    cfg = cfg or load_config()
    q = (question or "").lower()
    for alias in cfg.scope.get("out_of_scope_aliases") or []:
        if re.search(rf"\b{re.escape(alias.lower())}\b", q):
            return alias
    return None


def _refuse_text() -> str:
    path = ROOT / "prompts" / "refuse.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (
        "I can only share facts from official public pages, not investment advice "
        "such as whether to buy, sell, or how to build a portfolio."
    )
