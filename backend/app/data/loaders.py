"""Loaders for the mock datasets.

Two flavors of every accessor:
- raw (with ground-truth labels) — used ONLY by the eval module.
- LLM-safe (labels stripped) — everything that can reach a prompt.

Ground-truth columns are the labels/notes the judge must never see. They are
stripped centrally by ``strip_ground_truth`` so a new caller can't leak them.
"""

from __future__ import annotations

import csv
import json
import logging
from functools import lru_cache
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Columns hidden from the LLM (ground truth for evals only).
GROUND_TRUTH_COLUMNS = frozenset(
    {
        "case_label",
        "transaction_label",
        "label",
        "label_reason",
        "review_note",
        "risk_score",
        "audit_flag",
    }
)


def _data_dir() -> Path:
    base = Path(settings.data_dir)
    if not base.is_absolute():
        # settings.data_dir is relative to the backend/ working dir.
        base = (Path(__file__).resolve().parents[3] / "backend" / base).resolve()
    return base


def data_path(*parts: str) -> Path:
    return _data_dir().joinpath(*parts)


def strip_ground_truth(row: dict) -> dict:
    """Return a copy of ``row`` with all ground-truth columns removed."""
    return {k: v for k, v in row.items() if k not in GROUND_TRUTH_COLUMNS}


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- #
# Mock application overlay                                                     #
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def load_overlay() -> dict[str, dict]:
    """business_id -> overlay dict (naics, type, employees, certs, ...)."""
    path = data_path("mock_applications.json")
    records = json.loads(path.read_text(encoding="utf-8"))
    return {r["business_id"]: r for r in records}


# --------------------------------------------------------------------------- #
# Financial submission packages (FS*)                                         #
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _fs_index() -> dict[str, dict]:
    rows = _read_csv(data_path("financial_submission_examples", "index.csv"))
    return {r["business_id"]: r for r in rows}


@lru_cache(maxsize=1)
def _fs_packages_raw() -> dict[str, list[dict]]:
    """business_id -> list of line-item rows (WITH labels)."""
    index = _fs_index()
    file_by_id = {r["business_id"]: r["file_name"] for r in index.values()}
    out: dict[str, list[dict]] = {}
    for business_id, file_name in file_by_id.items():
        out[business_id] = _read_csv(
            data_path("financial_submission_examples", file_name)
        )
    return out


def fs_business_ids() -> list[str]:
    return sorted(_fs_index().keys())


def fs_case_label(business_id: str) -> str | None:
    row = _fs_index().get(business_id)
    return row["case_label"] if row else None


def fs_package_safe(business_id: str) -> list[dict]:
    """Line-item rows with ground truth stripped (LLM-safe)."""
    return [strip_ground_truth(r) for r in _fs_packages_raw().get(business_id, [])]


def fs_annual_revenue(business_id: str) -> float | None:
    """Derived gross revenue from the P&L rows."""
    for row in _fs_packages_raw().get(business_id, []):
        if row.get("record_type") == "profit_and_loss" and row.get("line_item") == "gross_revenue":
            try:
                return float(row.get("amount") or 0)
            except ValueError:
                return None
    return None


def fs_use_of_proceeds(business_id: str) -> list[dict]:
    """LLM-safe use-of-proceeds line items (feeds E3)."""
    rows = []
    for row in fs_package_safe(business_id):
        if row.get("record_type") == "use_of_proceeds":
            rows.append(
                {
                    "line_item": row.get("line_item"),
                    "description": row.get("description"),
                    "amount": _to_float(row.get("amount")),
                    "source_document": row.get("source_document"),
                    "counterparty": row.get("counterparty"),
                }
            )
    return rows


# --------------------------------------------------------------------------- #
# Business transaction files (BIZ*)                                           #
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _biz_index() -> dict[str, dict]:
    rows = _read_csv(data_path("sba_business_examples", "index.csv"))
    return {r["business_id"]: r for r in rows}


@lru_cache(maxsize=1)
def _biz_transactions_raw() -> dict[str, list[dict]]:
    index = _biz_index()
    file_by_id = {r["business_id"]: r["file_name"] for r in index.values()}
    out: dict[str, list[dict]] = {}
    for business_id, file_name in file_by_id.items():
        out[business_id] = _read_csv(data_path("sba_business_examples", file_name))
    return out


def biz_business_ids() -> list[str]:
    return sorted(_biz_index().keys())


def biz_case_label(business_id: str) -> str | None:
    row = _biz_index().get(business_id)
    return row["case_label"] if row else None


def biz_transactions_safe(business_id: str) -> list[dict]:
    """Transactions with ground truth stripped, one dict per row with a stable ``row`` index."""
    out = []
    for i, row in enumerate(_biz_transactions_raw().get(business_id, [])):
        out.append(
            {
                "row": i,
                "date": row.get("transaction_date"),
                "amount": _to_float(row.get("amount")),
                "category": row.get("category"),
                "merchant": row.get("merchant"),
                "description": row.get("description"),
                "owner_related": _to_bool(row.get("owner_related")),
                "documentation": row.get("documentation"),
            }
        )
    return out


def biz_transaction_labels(business_id: str) -> list[str]:
    """Ground-truth per-transaction labels (eval only)."""
    return [r.get("transaction_label", "") for r in _biz_transactions_raw().get(business_id, [])]


# --------------------------------------------------------------------------- #
# Eval-only CSV corpora                                                        #
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def synthetic_transactions() -> list[dict]:
    return _read_csv(data_path("synthetic_sba_audit_transactions.csv"))


@lru_cache(maxsize=1)
def pooled_transactions() -> list[dict]:
    rows: list[dict] = []
    for name in (
        "sba_transactions_not_flagged.csv",
        "sba_transactions_flagged.csv",
        "sba_transactions_mixed_review.csv",
    ):
        rows.extend(_read_csv(data_path(name)))
    return rows


@lru_cache(maxsize=1)
def borrower_risk_summary() -> list[dict]:
    return _read_csv(data_path("sba_borrower_risk_summary.csv"))


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _to_float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _to_bool(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes")
