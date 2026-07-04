"""Finding schema shared by both surfaces (eligibility + audit)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator

Verdict = Literal["pass", "flag", "fail"]


class Finding(BaseModel):
    check_id: str
    subject: str = "application"  # "application" or "transaction:<row>"
    verdict: Verdict
    confidence: float
    citation: str
    cited_text: str
    rationale: str

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))


# JSON schema for a single finding's *judged* fields — the model returns these
# and we merge in check_id/subject/citation server-side so it can't drift.
JUDGMENT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "flag", "fail"]},
        "confidence": {"type": "number"},
        "cited_text": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["verdict", "confidence", "cited_text", "rationale"],
    "additionalProperties": False,
}


# JSON schema for the batched transaction audit: one judgment per transaction row.
AUDIT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "row": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["pass", "flag", "fail"]},
                    "confidence": {"type": "number"},
                    "cited_text": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["row", "verdict", "confidence", "cited_text", "rationale"],
                "additionalProperties": False,
            },
        },
        "rollup": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["clear", "needs_review", "possible_fraud"],
                },
                "rationale": {"type": "string"},
            },
            "required": ["verdict", "rationale"],
            "additionalProperties": False,
        },
    },
    "required": ["transactions", "rollup"],
    "additionalProperties": False,
}
