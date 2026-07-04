"""Prompt builders for the judge.

Every builder receives already-LLM-safe data (ground truth stripped upstream in
loaders.py) plus the retrieved Title 13 section text. No ground-truth field is
ever interpolated here.
"""

from __future__ import annotations

import json

SYSTEM_PROMPT = (
    "You are an SBA loan-compliance reviewer. You validate loan applications and "
    "fund usage against Title 13 CFR Chapter I (the real SBA regulations). "
    "You are given the verbatim text of the relevant regulation section(s) and "
    "structured application/transaction data. Judge ONLY against the regulation "
    "text provided.\n\n"
    "Verdicts:\n"
    "- pass: clearly compliant.\n"
    "- flag: ambiguous, needs human review, or you are not confident.\n"
    "- fail: a clear violation of the cited regulation.\n\n"
    "CONFIDENCE — score how strongly the regulation text + the provided data "
    "settle this question. Use this rubric and report a precise value in [0,1] "
    "(two decimals). Do NOT default to round numbers like 0.9 or 0.95 — pick the "
    "value the evidence actually warrants:\n"
    "- 0.90-1.00: the regulation names this exact case AND the data states it "
    "plainly. No inference (e.g. a non-profit vs. §120.110(a)'s explicit list).\n"
    "- 0.75-0.89: a clear rule, but applying it takes one short inference step.\n"
    "- 0.60-0.74: the rule is general/principle-based, or the data is partial; "
    "reasonable reviewers could differ.\n"
    "- 0.40-0.59: weak signal — missing documentation, vague description, or no "
    "on-point section.\n"
    "- below 0.40: essentially no basis to decide.\n"
    "Calibrate within a band: stronger evidence sits higher in its range. "
    "Consistency rule: if your confidence is below 0.60, the verdict MUST be "
    "'flag' (not pass/fail) — low confidence means a human should review.\n\n"
    "Always ground your rationale in the provided regulation text and quote the "
    "specific excerpt you relied on in cited_text. Be concise and defensible — a "
    "loan officer reads this."
)


def _sections_block(sections: list[dict]) -> str:
    parts = []
    for s in sections:
        parts.append(f"§ {s['section']} — {s['heading']}\n{s['text']}")
    return "\n\n---\n\n".join(parts)


def eligibility_user_prompt(
    check_id: str,
    check_description: str,
    application: dict,
    sections: list[dict],
    extra_context: str = "",
) -> str:
    return (
        f"CHECK {check_id}: {check_description}\n\n"
        f"RELEVANT REGULATION TEXT:\n{_sections_block(sections)}\n\n"
        f"APPLICATION DATA:\n{json.dumps(application, indent=2)}\n"
        f"{extra_context}\n\n"
        "Produce your judgment for this single check."
    )


def audit_user_prompt(
    business: dict,
    transactions: list[dict],
    sections: list[dict],
) -> str:
    return (
        "Audit whether each transaction below is an eligible use of loan proceeds "
        f"under the {business.get('program')} program, judged against the regulation "
        "text. Then produce a business-level rollup verdict.\n\n"
        f"RELEVANT REGULATION TEXT:\n{_sections_block(sections)}\n\n"
        f"BUSINESS:\n{json.dumps(business, indent=2)}\n\n"
        f"TRANSACTIONS (judge each by its 'row'):\n{json.dumps(transactions, indent=2)}\n\n"
        "Return a judgment for every transaction row and one rollup "
        "(clear | needs_review | possible_fraud)."
    )
