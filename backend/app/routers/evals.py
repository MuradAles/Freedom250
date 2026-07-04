"""Eval endpoint: agreement stats vs. CSV ground truth."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.llm import eval as eval_mod
from app.llm.client import LLMNotConfigured

router = APIRouter(prefix="/api", tags=["eval"])


@router.get("/eval")
def get_eval(
    limit: int = Query(30, ge=1, le=60, description="Transaction rows to judge"),
    include_business: bool = Query(False),
    include_submission: bool = Query(False),
) -> dict:
    try:
        return eval_mod.run_all(
            limit=limit,
            include_business=include_business,
            include_submission=include_submission,
        )
    except LLMNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
