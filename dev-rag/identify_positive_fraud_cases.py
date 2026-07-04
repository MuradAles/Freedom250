"""Identify likely positive SBA fraud cases for human review."""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


DEFAULT_OUTPUT = Path("data/sba-fraud-dataset/positive_case_predictions.jsonl")
DEFAULT_MANIFEST = Path(
    "data/sba-fraud-dataset/positive_case_predictions_manifest.json"
)

ADJUDICATED_PATTERNS = (
    r"\bpleaded guilty\b",
    r"\bpleads guilty\b",
    r"\bguilty plea\b",
    r"\bconvicted\b",
    r"\bfound guilty\b",
    r"\bjury .* guilty\b",
    r"\bsentenced\b",
    r"\bordered to pay\b",
    r"\brestitution\b",
)
FRAUD_PATTERNS = (
    r"\bfraud\b",
    r"\bfraudulent\b",
    r"\bfraudulently\b",
    r"\bfalse\b",
    r"\bfictitious\b",
    r"\bconverted\b.*\bproceeds\b",
    r"\bconverting\b.*\bproceeds\b",
    r"\bmisused\b",
    r"\bmisappropriated\b",
    r"\bpersonal enrichment\b",
    r"\bwire fraud\b",
    r"\bbank fraud\b",
    r"\bfalse statement",
    r"\bstole\b",
    r"\bobtained.*fraud",
)
SBA_PROGRAM_PATTERNS = (
    r"\bPPP\b",
    r"\bPaycheck Protection Program\b",
    r"\bEIDL\b",
    r"\bEconomic Injury Disaster Loan\b",
    r"\bSmall Business Administration\b",
    r"\bSBA\b",
    r"\bCARES Act\b",
)
PROGRAM_EXCLUSION_PATTERNS = (
    r"\bunrelated to (?:SBA|PPP|EIDL|Paycheck Protection Program|Economic Injury Disaster Loan)\b",
    r"\bnot (?:an? )?(?:SBA|PPP|EIDL|Paycheck Protection Program|Economic Injury Disaster Loan)\b",
)
ALLEGATION_ONLY_PATTERNS = (
    r"\bindicted\b",
    r"\bcharged\b",
    r"\bcriminal complaint\b",
    r"\balleged\b",
    r"\ballegations\b",
)


@dataclass(frozen=True)
class PositiveCasePrediction:
    """Model output for one potential positive fraud case."""

    record_id: str | None
    source_record_type: str | None
    predicted_label: int | None
    label_status: str
    score: float
    reasons: list[str]
    source_url: str | None
    summary: str | None


def _matches(patterns: Sequence[str], text: str) -> list[str]:
    return [
        pattern
        for pattern in patterns
        if re.search(pattern, text, flags=re.IGNORECASE)
    ]


def _record_text(record: dict[str, Any]) -> str:
    parts = []
    for key in (
        "outcome",
        "label_basis",
        "source_document_type",
        "case_name",
        "borrower_name",
        "program",
        "summary",
        "text",
        "citation",
    ):
        value = record.get(key)
        if value is not None:
            parts.append(str(value))
    source = record.get("source")
    if isinstance(source, dict):
        parts.extend(str(value) for value in source.values() if value)
    return " ".join(parts)


def predict_positive_case(record: dict[str, Any]) -> PositiveCasePrediction:
    """Score whether a record is an evidence-backed positive fraud case."""
    existing_label = record.get("training_label")
    existing_status = record.get("label_status")
    if existing_label == 1 or existing_label == "1":
        return PositiveCasePrediction(
            record_id=record.get("record_id") or record.get("case_id"),
            source_record_type=record.get("record_type"),
            predicted_label=1,
            label_status="already_verified_positive",
            score=1.0,
            reasons=["record already has training_label=1"],
            source_url=record.get("source_url"),
            summary=record.get("summary"),
        )
    if existing_status == "unlabeled_control":
        return PositiveCasePrediction(
            record_id=record.get("record_id") or record.get("case_id"),
            source_record_type=record.get("record_type"),
            predicted_label=None,
            label_status="not_a_case_record",
            score=0.0,
            reasons=["public loan controls are not fraud case records"],
            source_url=record.get("source_url"),
            summary=record.get("summary"),
        )

    text = _record_text(record)
    adjudicated = _matches(ADJUDICATED_PATTERNS, text)
    fraud = _matches(FRAUD_PATTERNS, text)
    program = _matches(SBA_PROGRAM_PATTERNS, text)
    if _matches(PROGRAM_EXCLUSION_PATTERNS, text):
        program = []
    allegation_only = _matches(ALLEGATION_ONLY_PATTERNS, text)

    reasons = []
    score = 0.0
    if adjudicated:
        score += 0.45
        reasons.append("adjudicated outcome language")
    if fraud:
        score += 0.25
        reasons.append("fraud scheme language")
    if program:
        score += 0.25
        reasons.append("SBA/PPP/EIDL program language")
    if allegation_only and not adjudicated:
        score += 0.15
        reasons.append("allegation-only language")

    score = min(score, 1.0)
    if adjudicated and fraud and program:
        label_status = "likely_verified_positive"
        predicted_label = 1
    elif allegation_only or (fraud and program):
        label_status = "review_required"
        predicted_label = None
    else:
        label_status = "not_positive_case"
        predicted_label = None
    if not reasons:
        reasons.append("insufficient positive case signals")

    return PositiveCasePrediction(
        record_id=record.get("record_id") or record.get("case_id"),
        source_record_type=record.get("record_type"),
        predicted_label=predicted_label,
        label_status=label_status,
        score=round(score, 4),
        reasons=reasons,
        source_url=record.get("source_url"),
        summary=record.get("summary"),
    )


def load_records(path: str | Path) -> list[dict[str, Any]]:
    """Load source records from JSONL or CSV."""
    source = Path(path)
    if source.suffix.lower() == ".csv":
        with source.open(encoding="utf-8-sig", newline="") as file:
            return list(csv.DictReader(file))
    records = []
    with source.open(encoding="utf-8") as file:
        for line in file:
            if line.strip():
                records.append(json.loads(line))
    return records


def write_predictions(
    predictions: Iterable[PositiveCasePrediction],
    output_path: str | Path = DEFAULT_OUTPUT,
) -> int:
    """Write predictions to JSONL and return the record count."""
    values = list(predictions)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "".join(json.dumps(asdict(value), sort_keys=True) + "\n" for value in values),
        encoding="utf-8",
    )
    return len(values)


def identify_positive_cases(
    input_path: str | Path,
    output_path: str | Path = DEFAULT_OUTPUT,
    manifest_path: str | Path = DEFAULT_MANIFEST,
) -> dict[str, Any]:
    """Run the triage model over source records and write artifacts."""
    records = load_records(input_path)
    predictions = [predict_positive_case(record) for record in records]
    write_predictions(predictions, output_path)
    counts: dict[str, int] = {}
    for prediction in predictions:
        counts[prediction.label_status] = counts.get(prediction.label_status, 0) + 1
    manifest = {
        "model": "rule_based_positive_case_identifier_v1",
        "purpose": (
            "Triage public court and enforcement records for likely positive "
            "SBA fraud cases. This is not a borrower risk model."
        ),
        "input": str(input_path),
        "output": str(output_path),
        "record_count": len(predictions),
        "label_status_counts": counts,
        "limitations": [
            "Requires human review before adding new training labels.",
            "Does not identify non-fraudulent borrowers.",
            "Should not be used for credit, eligibility, enforcement, or adverse decisions.",
        ],
    }
    Path(manifest_path).write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Identify likely positive SBA fraud case records."
    )
    parser.add_argument(
        "input",
        help="JSONL or CSV case records, such as court_candidates.jsonl",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)

    manifest = identify_positive_cases(
        args.input,
        output_path=args.output,
        manifest_path=args.manifest,
    )
    print(f"record_count: {manifest['record_count']}")
    for status, count in sorted(manifest["label_status_counts"].items()):
        print(f"{status}: {count}")
    print(f"output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
