"""Citation drawer endpoint: resolve any Title 13 citation to its section text."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.store import regulations

router = APIRouter(prefix="/api", tags=["regulations"])


@router.get("/regulations/{citation:path}")
def get_regulation(citation: str) -> dict:
    chunk = regulations().resolve(citation)
    if chunk is None:
        raise HTTPException(status_code=404, detail="citation not found")
    return {
        "citation": f"13 CFR § {chunk.section}",
        "part": chunk.part,
        "section": chunk.section,
        "heading": chunk.heading,
        "text": chunk.text,
    }
