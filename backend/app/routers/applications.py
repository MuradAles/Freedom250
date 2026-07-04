"""Applications endpoints: list, detail, eligibility check, transaction audit."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.data import loaders
from app.llm import audit as audit_mod
from app.llm import checks as checks_mod
from app.llm.client import LLMNotConfigured
from app.store import cache_audit, cache_eligibility, get_cached

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["applications"])


def _kind(business_id: str) -> str:
    return "audit" if business_id.startswith("BIZ") else "application"


def _status(borrower_id: str) -> str:
    cached = get_cached(borrower_id)
    findings = list(cached.get("eligibility", [])) + list(cached.get("audit", []))
    if not findings:
        return "not_run"
    verdicts = {f.verdict for f in findings}
    if "fail" in verdicts:
        return "failed"
    if "flag" in verdicts:
        return "flagged"
    return "clear"


def _record(business_id: str) -> dict:
    overlay = loaders.load_overlay().get(business_id, {})
    return {
        "borrower_id": business_id,
        "business_name": overlay.get("business_name"),
        "program": overlay.get("program"),
        "amount": overlay.get("loan_amount"),
        "kind": _kind(business_id),
        "status": _status(business_id),
    }


@router.get("/applications")
def list_applications() -> list[dict]:
    ids = loaders.fs_business_ids() + loaders.biz_business_ids()
    return [_record(bid) for bid in ids]


@router.get("/applications/{borrower_id}")
def get_application(borrower_id: str) -> dict:
    overlay = loaders.load_overlay().get(borrower_id)
    if overlay is None:
        raise HTTPException(status_code=404, detail="borrower not found")

    kind = _kind(borrower_id)
    application = None
    transactions = None
    if kind == "application":
        application = {
            **overlay,
            "annual_revenue": loaders.fs_annual_revenue(borrower_id),
            "use_of_proceeds": loaders.fs_use_of_proceeds(borrower_id),
        }
    else:
        transactions = loaders.biz_transactions_safe(borrower_id)

    cached = get_cached(borrower_id)
    return {
        "borrower_id": borrower_id,
        "business_name": overlay.get("business_name"),
        "program": overlay.get("program"),
        "amount": overlay.get("loan_amount"),
        "kind": kind,
        "application": application,
        "transactions": transactions,
        "eligibility_findings": [f.model_dump() for f in cached.get("eligibility", [])],
        "audit_findings": [f.model_dump() for f in cached.get("audit", [])],
        "rollup": cached.get("rollup"),
    }


@router.post("/applications/{borrower_id}/check")
def run_check(borrower_id: str) -> dict:
    if loaders.load_overlay().get(borrower_id) is None:
        raise HTTPException(status_code=404, detail="borrower not found")
    try:
        findings = checks_mod.run_eligibility(borrower_id)
    except LLMNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    cache_eligibility(borrower_id, findings)
    return {"eligibility_findings": [f.model_dump() for f in findings]}


@router.post("/applications/{borrower_id}/audit")
def run_audit(borrower_id: str) -> dict:
    if loaders.load_overlay().get(borrower_id) is None:
        raise HTTPException(status_code=404, detail="borrower not found")
    try:
        findings, rollup = audit_mod.run_audit(borrower_id)
    except LLMNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    cache_audit(borrower_id, findings, rollup)
    return {
        "audit_findings": [f.model_dump() for f in findings],
        "rollup": rollup,
    }
