"""Build a provenance-aware SBA fraud research dataset."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable, Iterator, Sequence


DEFAULT_OUTPUT_DIR = Path("data/sba-fraud-dataset")
SBA_PPP_CATALOG_URL = (
    "https://data.sba.gov/api/3/action/package_show?id=ppp-foia"
)
COURTLISTENER_SEARCH_URL = (
    "https://www.courtlistener.com/api/rest/v3/search/"
)
USER_AGENT = "fwa-legal-rag/1.0 (SBA fraud dataset research)"
POSITIVE_OUTCOMES = {"convicted", "guilty_plea", "civil_judgment"}
NEGATIVE_OUTCOMES = {"audited_no_fraud"}
REVIEW_OUTCOMES = {
    "alleged",
    "acquitted",
    "dismissed",
    "civil_settlement_no_admission",
}
ALLOWED_OUTCOMES = POSITIVE_OUTCOMES | NEGATIVE_OUTCOMES | REVIEW_OUTCOMES
COURT_QUERIES = (
    '"Paycheck Protection Program" fraud',
    '"Economic Injury Disaster Loan" fraud',
    '"SBA loan" fraud',
)


@dataclass(frozen=True)
class DatasetOutcome:
    """Summary of one dataset build."""

    controls: int
    verified_cases: int
    court_candidates: int
    trainable_records: int
    output_dir: Path


def _request_json(
    url: str,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def _open_url(url: str) -> BinaryIO:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=180)


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    values = list(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in values),
        encoding="utf-8",
    )
    return len(values)


def _record_id(namespace: str, value: str) -> str:
    digest = hashlib.sha256(f"{namespace}:{value}".encode("utf-8")).hexdigest()
    return digest[:24]


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def discover_ppp_resources(
    catalog: dict[str, Any],
    include_small_loans: bool = False,
) -> list[dict[str, Any]]:
    """Return current official SBA PPP CSV resources."""
    if not catalog.get("success"):
        raise ValueError("SBA catalog response was not successful")
    resources = []
    for resource in catalog.get("result", {}).get("resources", []):
        name = str(resource.get("name", ""))
        if str(resource.get("format", "")).upper() != "CSV":
            continue
        if not include_small_loans and "150k_plus" not in name.lower():
            continue
        if not resource.get("url"):
            continue
        resources.append(resource)
    if not resources:
        raise ValueError("No matching PPP CSV resources found in SBA catalog")
    return sorted(resources, key=lambda item: str(item.get("name", "")))


def _ppp_control(row: dict[str, Any], resource: dict[str, Any]) -> dict[str, Any]:
    loan_number = _clean(row.get("LoanNumber"))
    if not loan_number:
        raise ValueError("PPP record is missing LoanNumber")
    return {
        "record_id": _record_id("sba-ppp", loan_number),
        "record_type": "loan_control",
        "training_label": None,
        "label_status": "unlabeled_control",
        "label_basis": (
            "Public SBA loan record with no linked adjudicated fraud outcome. "
            "Absence of a match does not establish that the loan was legitimate."
        ),
        "program": "PPP",
        "loan_number": loan_number,
        "borrower_name": _clean(row.get("BorrowerName")),
        "borrower_city": _clean(
            row.get("BorrowerCity") or row.get("ProjectCity")
        ),
        "borrower_state": _clean(
            row.get("BorrowerState") or row.get("ProjectState")
        ),
        "approval_date": _clean(row.get("DateApproved")),
        "approval_amount": _clean(row.get("CurrentApprovalAmount")),
        "loan_status": _clean(row.get("LoanStatus")),
        "jobs_reported": _clean(row.get("JobsReported")),
        "naics_code": _clean(row.get("NAICSCode")),
        "source": {
            "publisher": "U.S. Small Business Administration",
            "dataset": "PPP FOIA",
            "resource_id": resource.get("id"),
            "resource_name": resource.get("name"),
            "resource_url": resource.get("url"),
            "resource_modified": resource.get("last_modified"),
        },
        "source_record": row,
    }


def iter_ppp_controls(
    resources: Sequence[dict[str, Any]],
    max_records: int,
    opener: Callable[[str], BinaryIO] = _open_url,
) -> Iterator[dict[str, Any]]:
    """Stream normalized PPP controls without retaining full source files."""
    remaining = max_records
    for resource in resources:
        if max_records and remaining <= 0:
            return
        with opener(str(resource["url"])) as response:
            text = io.TextIOWrapper(
                response,
                encoding="utf-8-sig",
                errors="replace",
                newline="",
            )
            for row in csv.DictReader(text):
                yield _ppp_control(row, resource)
                if max_records:
                    remaining -= 1
                    if remaining <= 0:
                        return


def _case_label(outcome: str) -> tuple[int | None, str]:
    if outcome in POSITIVE_OUTCOMES:
        return 1, "verified_positive"
    if outcome in NEGATIVE_OUTCOMES:
        return 0, "verified_negative"
    return None, "review_only"


def normalize_verified_case(row: dict[str, Any]) -> dict[str, Any]:
    """Validate a manually reviewed court or audit outcome."""
    outcome = str(row.get("outcome", "")).strip().lower()
    if outcome not in ALLOWED_OUTCOMES:
        raise ValueError(
            "Unsupported case outcome. Expected one of: "
            + ", ".join(sorted(ALLOWED_OUTCOMES))
        )
    source_url = _clean(row.get("source_url"))
    docket_number = _clean(row.get("docket_number"))
    if outcome in POSITIVE_OUTCOMES | NEGATIVE_OUTCOMES:
        if not source_url and not docket_number:
            raise ValueError(
                f"{outcome} requires a source_url or docket_number"
            )

    case_id = (
        _clean(row.get("case_id"))
        or docket_number
        or source_url
        or json.dumps(row, sort_keys=True)
    )
    training_label, label_status = _case_label(outcome)
    return {
        "record_id": _record_id("verified-case", str(case_id)),
        "record_type": "verified_case",
        "training_label": training_label,
        "label_status": label_status,
        "label_basis": outcome,
        "program": _clean(row.get("program")),
        "loan_number": _clean(row.get("loan_number")),
        "borrower_name": _clean(row.get("borrower_name")),
        "case_id": _clean(row.get("case_id")),
        "outcome": outcome,
        "court": _clean(row.get("court")),
        "docket_number": docket_number,
        "disposition_date": _clean(row.get("disposition_date")),
        "source_document_type": _clean(row.get("source_document_type")),
        "source_url": source_url,
        "summary": _clean(row.get("summary")),
        "reviewed_by": _clean(row.get("reviewed_by")),
        "reviewed_at": _clean(row.get("reviewed_at")),
    }


def load_verified_cases(path: str | Path) -> list[dict[str, Any]]:
    """Load reviewed cases from CSV or JSONL."""
    source = Path(path)
    if source.suffix.lower() == ".jsonl":
        rows = [
            json.loads(line)
            for line in source.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        with source.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
    return [normalize_verified_case(row) for row in rows]


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def _court_candidate(
    result: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    identifier = str(
        result.get("cluster_id")
        or result.get("docket_id")
        or result.get("id")
        or result.get("absolute_url")
    )
    absolute_url = str(result.get("absolute_url") or "")
    if absolute_url.startswith("/"):
        absolute_url = f"https://www.courtlistener.com{absolute_url}"
    return {
        "record_id": _record_id("courtlistener", identifier),
        "record_type": "court_candidate",
        "training_label": None,
        "label_status": "review_required",
        "label_basis": (
            "Matched an SBA-fraud search query. Review the underlying docket "
            "and disposition before assigning a training label."
        ),
        "query": query,
        "case_name": _clean(result.get("caseName")),
        "court": _clean(
            result.get("court_citation_string") or result.get("court")
        ),
        "docket_number": _clean(result.get("docketNumber")),
        "date_filed": _clean(result.get("dateFiled")),
        "source_url": absolute_url or None,
        "summary": _strip_html(str(result.get("snippet") or "")) or None,
        "source": {
            "publisher": "Free Law Project",
            "dataset": "CourtListener RECAP",
        },
    }


def fetch_court_candidates(
    api_token: str,
    max_records: int,
    fetch_json: Callable[..., dict[str, Any]] = _request_json,
    queries: Sequence[str] = COURT_QUERIES,
) -> list[dict[str, Any]]:
    """Fetch unverified SBA fraud candidates from CourtListener."""
    if not api_token.strip():
        return []
    candidates: dict[str, dict[str, Any]] = {}
    per_query = max(1, max_records // len(queries)) if max_records else 20
    headers = {"Authorization": f"Token {api_token}"}
    for query in queries:
        params = urllib.parse.urlencode(
            {
                "q": query,
                "type": "r",
                "order_by": "dateFiled desc",
            }
        )
        page = fetch_json(
            f"{COURTLISTENER_SEARCH_URL}?{params}",
            headers=headers,
        )
        for result in page.get("results", [])[:per_query]:
            candidate = _court_candidate(result, query)
            candidates[candidate["record_id"]] = candidate
            if max_records and len(candidates) >= max_records:
                return list(candidates.values())
    return list(candidates.values())


def build_sba_fraud_dataset(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    max_controls: int = 1000,
    include_small_loans: bool = False,
    case_files: Sequence[str | Path] = (),
    courtlistener_token: str | None = None,
    max_court_candidates: int = 100,
    fetch_json: Callable[..., dict[str, Any]] = _request_json,
    opener: Callable[[str], BinaryIO] = _open_url,
) -> DatasetOutcome:
    """Build controls, reviewed outcomes, and court-review candidates."""
    if max_controls < 0 or max_court_candidates < 0:
        raise ValueError("Record limits must be zero or greater")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    catalog = fetch_json(SBA_PPP_CATALOG_URL)
    resources = discover_ppp_resources(
        catalog,
        include_small_loans=include_small_loans,
    )
    controls = list(iter_ppp_controls(resources, max_controls, opener=opener))

    verified_cases = []
    for case_file in case_files:
        verified_cases.extend(load_verified_cases(case_file))

    court_candidates = fetch_court_candidates(
        courtlistener_token or "",
        max_court_candidates,
        fetch_json=fetch_json,
    )
    dataset = [*verified_cases, *court_candidates, *controls]
    trainable = [
        record for record in dataset if record["training_label"] in (0, 1)
    ]

    _write_jsonl(destination / "controls.jsonl", controls)
    _write_jsonl(destination / "verified_cases.jsonl", verified_cases)
    _write_jsonl(destination / "court_candidates.jsonl", court_candidates)
    _write_jsonl(destination / "dataset.jsonl", dataset)
    _write_jsonl(destination / "trainable.jsonl", trainable)

    built_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema_version": 1,
        "built_at": built_at,
        "counts": {
            "controls": len(controls),
            "verified_cases": len(verified_cases),
            "court_candidates": len(court_candidates),
            "trainable_records": len(trainable),
        },
        "labels": {
            "1": sorted(POSITIVE_OUTCOMES),
            "0": sorted(NEGATIVE_OUTCOMES),
            "null": sorted(REVIEW_OUTCOMES)
            + ["court_candidate", "unlabeled_control"],
        },
        "warnings": [
            (
                "SBA public loan records are unlabeled controls, not confirmed "
                "non-fraudulent applications."
            ),
            (
                "CourtListener search matches require human review and are "
                "excluded from trainable.jsonl until verified."
            ),
            (
                "Do not use this dataset as the sole basis for lending, "
                "eligibility, enforcement, or other adverse decisions."
            ),
        ],
        "sources": {
            "sba_ppp_catalog": SBA_PPP_CATALOG_URL,
            "sba_resources": [
                {
                    "id": resource.get("id"),
                    "name": resource.get("name"),
                    "url": resource.get("url"),
                    "last_modified": resource.get("last_modified"),
                }
                for resource in resources
            ],
            "courtlistener": (
                COURTLISTENER_SEARCH_URL if courtlistener_token else None
            ),
            "verified_case_files": [str(Path(path)) for path in case_files],
        },
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    ledger_path = destination / "download-ledger.json"
    ledger = {"schema_version": 1, "builds": []}
    if ledger_path.is_file():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger.setdefault("builds", []).append(
        {
            "built_at": built_at,
            "counts": manifest["counts"],
            "resource_ids": [
                resource.get("id") for resource in resources
            ],
        }
    )
    ledger["builds"] = ledger["builds"][-100:]
    ledger_path.write_text(
        json.dumps(ledger, indent=2) + "\n",
        encoding="utf-8",
    )

    return DatasetOutcome(
        len(controls),
        len(verified_cases),
        len(court_candidates),
        len(trainable),
        destination,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a provenance-aware SBA fraud research dataset."
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--max-controls",
        type=int,
        default=1000,
        help="Maximum PPP control records; use 0 for every selected record",
    )
    parser.add_argument(
        "--include-small-loans",
        action="store_true",
        help="Include all official PPP CSV partitions, not only $150k+ loans",
    )
    parser.add_argument(
        "--case-file",
        action="append",
        default=[],
        help="Reviewed case CSV or JSONL; may be supplied more than once",
    )
    parser.add_argument(
        "--courtlistener-token",
        default=os.environ.get("COURTLISTENER_API_TOKEN"),
        help="CourtListener API token or COURTLISTENER_API_TOKEN",
    )
    parser.add_argument("--max-court-candidates", type=int, default=100)
    args = parser.parse_args(argv)

    outcome = build_sba_fraud_dataset(
        output_dir=args.output_dir,
        max_controls=args.max_controls,
        include_small_loans=args.include_small_loans,
        case_files=args.case_file,
        courtlistener_token=args.courtlistener_token,
        max_court_candidates=args.max_court_candidates,
    )
    print(f"controls: {outcome.controls}")
    print(f"verified_cases: {outcome.verified_cases}")
    print(f"court_candidates: {outcome.court_candidates}")
    print(f"trainable_records: {outcome.trainable_records}")
    print(f"output_dir: {outcome.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
