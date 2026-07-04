"""In-memory data singletons + per-borrower findings cache.

Regulations and size standards are parsed once at startup. Findings are cached
per borrower so revisiting a record doesn't re-spend tokens.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.data.loaders import data_path
from app.data.regulations import RegulationIndex, load_regulations
from app.data.size_standards import SizeStandard, load_size_standards

logger = logging.getLogger(__name__)

_regulations: RegulationIndex | None = None
_size_standards: dict[str, SizeStandard] | None = None

# borrower_id -> {"eligibility": [Finding...], "audit": [Finding...], "rollup": {...}}
_findings_cache: dict[str, dict] = {}


def load_all() -> None:
    """Parse regulations + size standards into memory. Call at startup."""
    global _regulations, _size_standards
    reg_path = data_path("title-13-chapter-i.txt")
    _regulations = load_regulations(reg_path)
    _size_standards = load_size_standards(reg_path)
    logger.info(
        "Loaded %d regulation sections, %d NAICS size standards",
        len(_regulations.chunks),
        len(_size_standards),
    )


def regulations() -> RegulationIndex:
    if _regulations is None:
        load_all()
    assert _regulations is not None
    return _regulations


def size_standards() -> dict[str, SizeStandard]:
    if _size_standards is None:
        load_all()
    assert _size_standards is not None
    return _size_standards


# --- findings cache ---
def get_cached(borrower_id: str) -> dict:
    return _findings_cache.get(borrower_id, {})


def cache_eligibility(borrower_id: str, findings: list) -> None:
    _findings_cache.setdefault(borrower_id, {})["eligibility"] = findings


def cache_audit(borrower_id: str, findings: list, rollup: dict) -> None:
    entry = _findings_cache.setdefault(borrower_id, {})
    entry["audit"] = findings
    entry["rollup"] = rollup
