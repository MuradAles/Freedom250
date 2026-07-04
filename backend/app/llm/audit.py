"""Transaction use-of-proceeds audit (batched per business) + rollup verdict."""

from __future__ import annotations

import logging

from app.data import loaders
from app.llm import prompts
from app.llm.client import judge
from app.llm.schema import AUDIT_JSON_SCHEMA, Finding
from app.store import regulations

logger = logging.getLogger(__name__)

# Audit judges spend against the general use-of-proceeds rules (+ Part 123 for EIDL).
AUDIT_CITATIONS = ["120.120", "120.130"]
EIDL_CITATIONS = ["123.300", "123.303"]


def _sections(program: str) -> list[dict]:
    reg = regulations()
    citations = list(AUDIT_CITATIONS)
    if "eidl" in (program or "").lower():
        citations += EIDL_CITATIONS
    out = []
    for c in citations:
        chunk = reg.resolve(c)
        if chunk:
            out.append({"section": chunk.section, "heading": chunk.heading, "text": chunk.text})
    return out


def _primary_citation(program: str) -> str:
    return "13 CFR § 120.120"


def run_audit(business_id: str) -> tuple[list[Finding], dict]:
    overlay = loaders.load_overlay().get(business_id, {})
    program = overlay.get("program") or ""
    business = {
        "business_id": business_id,
        "business_name": overlay.get("business_name"),
        "program": program,
        "loan_amount": overlay.get("loan_amount"),
    }
    transactions = loaders.biz_transactions_safe(business_id)
    sections = _sections(program)

    prompt = prompts.audit_user_prompt(business, transactions, sections)
    result = judge(prompts.SYSTEM_PROMPT, prompt, AUDIT_JSON_SCHEMA, schema_name="audit")

    citation = _primary_citation(program)
    by_row = {t["row"]: t for t in transactions}
    findings: list[Finding] = []
    for item in result.get("transactions", []):
        row = item.get("row")
        findings.append(
            Finding(
                check_id="AUDIT",
                subject=f"transaction:{row}",
                verdict=item["verdict"],
                confidence=float(item["confidence"]),
                citation=citation,
                cited_text=item.get("cited_text", ""),
                rationale=item["rationale"],
            )
        )
    rollup = result.get("rollup", {"verdict": "needs_review", "rationale": ""})
    return findings, rollup
