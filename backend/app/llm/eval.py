"""Built-in evals: transaction-, business-, and submission-level agreement.

Ground truth is read straight from the labeled CSVs here (never sent to the
judge). Each level compares LLM verdicts to the mapped ground-truth label and
reports agreement %, a confusion table, and the disagreeing rows.
"""

from __future__ import annotations

import logging

from app.data import loaders
from app.llm import prompts
from app.llm.client import judge
from app.llm.schema import AUDIT_JSON_SCHEMA
from app.store import regulations

logger = logging.getLogger(__name__)

# Ground-truth label -> expected verdict.
TXN_LABEL_MAP = {"allowed": "pass", "inappropriate": "fail", "questionable": "flag"}
SUBMISSION_LABEL_MAP = {"normal": "pass", "needs_review": "flag"}
BUSINESS_LABEL_MAP = {
    "not_flagged": "clear",
    "needs_review": "needs_review",
    "flagged_possible_fraud": "possible_fraud",
}


def _confusion(pairs: list[tuple[str, str]]) -> dict:
    table: dict[str, dict[str, int]] = {}
    for expected, got in pairs:
        table.setdefault(expected, {}).setdefault(got, 0)
        table[expected][got] += 1
    return table


def _audit_sections() -> list[dict]:
    reg = regulations()
    out = []
    for c in ("120.120", "120.130"):
        chunk = reg.resolve(c)
        if chunk:
            out.append({"section": chunk.section, "heading": chunk.heading, "text": chunk.text})
    return out


def _judge_transactions(rows: list[dict]) -> list[dict]:
    """Judge a batch of eval transactions (labels stripped)."""
    safe = []
    for i, r in enumerate(rows):
        safe.append(
            {
                "row": i,
                "date": r.get("transaction_date"),
                "amount": float(r.get("amount") or 0),
                "category": r.get("category"),
                "merchant": r.get("merchant"),
                "description": r.get("description"),
                "owner_related": str(r.get("owner_related")).lower() == "true",
                "documentation": r.get("documentation"),
            }
        )
    business = {"business_name": "eval batch", "program": "PPP/EIDL"}
    prompt = prompts.audit_user_prompt(business, safe, _audit_sections())
    result = judge(prompts.SYSTEM_PROMPT, prompt, AUDIT_JSON_SCHEMA, schema_name="audit")
    return result.get("transactions", [])


def transaction_level(limit: int | None = 30) -> dict:
    rows = loaders.synthetic_transactions()
    if limit:
        rows = rows[:limit]
    judged = _judge_transactions(rows)
    got_by_row = {j["row"]: j["verdict"] for j in judged}

    pairs: list[tuple[str, str]] = []
    disagreements = []
    for i, r in enumerate(rows):
        expected = TXN_LABEL_MAP.get((r.get("label") or "").lower())
        got = got_by_row.get(i)
        if expected is None or got is None:
            continue
        pairs.append((expected, got))
        if expected != got:
            disagreements.append(
                {
                    "id": r.get("borrower_id"),
                    "row": i,
                    "expected": expected,
                    "got": got,
                    "description": r.get("description"),
                }
            )
    agree = sum(1 for e, g in pairs if e == g)
    total = len(pairs)
    return {
        "total": total,
        "agree": agree,
        "agreement_pct": round(100 * agree / total, 1) if total else 0.0,
        "confusion": _confusion(pairs),
        "disagreements": disagreements,
    }


def submission_level() -> dict:
    from app.llm.checks import run_eligibility

    pairs: list[tuple[str, str]] = []
    disagreements = []
    for business_id in loaders.fs_business_ids():
        label = loaders.fs_case_label(business_id)
        expected = SUBMISSION_LABEL_MAP.get(label)
        if expected is None:
            continue
        findings = run_eligibility(business_id)
        e6 = next((f for f in findings if f.check_id == "E6"), None)
        got = e6.verdict if e6 else None
        if got is None:
            continue
        # E6 skews flag over fail; treat fail as flag for submission agreement.
        got_norm = "flag" if got == "fail" else got
        pairs.append((expected, got_norm))
        if expected != got_norm:
            disagreements.append(
                {"id": business_id, "expected": expected, "got": got_norm}
            )
    agree = sum(1 for e, g in pairs if e == g)
    total = len(pairs)
    return {
        "total": total,
        "agree": agree,
        "agreement_pct": round(100 * agree / total, 1) if total else 0.0,
        "confusion": _confusion(pairs),
        "disagreements": disagreements,
    }


def business_level() -> dict:
    from app.llm.audit import run_audit

    pairs: list[tuple[str, str]] = []
    disagreements = []
    for business_id in loaders.biz_business_ids():
        label = loaders.biz_case_label(business_id)
        expected = BUSINESS_LABEL_MAP.get(label)
        if expected is None:
            continue
        _findings, rollup = run_audit(business_id)
        got = rollup.get("verdict")
        if got is None:
            continue
        pairs.append((expected, got))
        if expected != got:
            disagreements.append({"id": business_id, "expected": expected, "got": got})
    agree = sum(1 for e, g in pairs if e == g)
    total = len(pairs)
    return {
        "total": total,
        "agree": agree,
        "agreement_pct": round(100 * agree / total, 1) if total else 0.0,
        "confusion": _confusion(pairs),
        "disagreements": disagreements,
    }


def run_all(limit: int | None = 30, include_business: bool = False, include_submission: bool = False) -> dict:
    """Transaction level always; business/submission optional (they cost more)."""
    out = {"transaction_level": transaction_level(limit=limit)}
    out["business_level"] = business_level() if include_business else None
    out["submission_level"] = submission_level() if include_submission else None
    return out
