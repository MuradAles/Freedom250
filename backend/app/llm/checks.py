"""Eligibility checks E1-E6.

Each check retrieves a fixed set of Title 13 sections (static map, no RAG),
optionally does deterministic pre-work (E1 size compare, E5 cert presence),
then asks the judge for a citation-backed verdict. E1 and E5 short-circuit the
LLM for the mechanical decision but still ask it for the rationale text.
"""

from __future__ import annotations

import logging

from app.data import loaders
from app.llm import prompts
from app.llm.client import judge
from app.llm.schema import JUDGMENT_JSON_SCHEMA, Finding
from app.store import regulations, size_standards

logger = logging.getLogger(__name__)

# Static retrieval map: check_id -> [citation, ...]
RETRIEVAL_MAP: dict[str, list[str]] = {
    "E1": ["121.201"],
    "E2": ["120.110"],
    "E3": ["120.120", "120.130"],
    "E4": ["123.200", "123.201", "123.300", "123.303"],
    "E5": ["112.2", "146.100"],
    "E6": ["120.150", "120.160"],
}

CHECK_DESCRIPTIONS = {
    "E1": "Business size vs. the size standard for its NAICS code (Part 121, §121.201).",
    "E2": "Eligible business type — not an ineligible business under §120.110.",
    "E3": "Intended use of proceeds is eligible (§120.120 eligible / §120.130 restricted).",
    "E4": "Disaster (EIDL) loan eligibility & use restrictions (Part 123).",
    "E5": "Required certifications present (nondiscrimination, anti-lobbying).",
    "E6": "Financial package review — repayment ability, documentation, consistency (§120.150/.160).",
}


def _sections_for(check_id: str) -> list[dict]:
    reg = regulations()
    out = []
    for citation in RETRIEVAL_MAP[check_id]:
        chunk = reg.resolve(citation)
        if chunk:
            out.append(
                {"section": chunk.section, "heading": chunk.heading, "text": chunk.text}
            )
        else:
            logger.warning("Check %s: citation %s did not resolve", check_id, citation)
    return out


def _primary_citation(check_id: str, sections: list[dict]) -> str:
    if sections:
        return f"13 CFR § {sections[0]['section']}"
    return f"13 CFR § {RETRIEVAL_MAP[check_id][0]}"


def _finding(check_id: str, judgment: dict, sections: list[dict]) -> Finding:
    return Finding(
        check_id=check_id,
        subject="application",
        verdict=judgment["verdict"],
        confidence=float(judgment["confidence"]),
        citation=_primary_citation(check_id, sections),
        cited_text=judgment.get("cited_text", ""),
        rationale=judgment["rationale"],
    )


def _application_view(business_id: str, overlay: dict) -> dict:
    """LLM-safe application summary shared across checks."""
    return {
        "business_id": business_id,
        "business_name": overlay.get("business_name"),
        "program": overlay.get("program"),
        "loan_amount": overlay.get("loan_amount"),
        "naics_code": overlay.get("naics_code"),
        "business_type": overlay.get("business_type"),
        "employee_count": overlay.get("employee_count"),
        "years_in_business": overlay.get("years_in_business"),
        "ownership": overlay.get("ownership"),
        "annual_revenue": loaders.fs_annual_revenue(business_id),
    }


def run_eligibility(business_id: str) -> list[Finding]:
    """Run all applicable eligibility checks for an FS application."""
    overlay = loaders.load_overlay().get(business_id)
    if not overlay:
        raise KeyError(f"No overlay for {business_id}")
    app_view = _application_view(business_id, overlay)
    program = (overlay.get("program") or "").lower()

    findings: list[Finding] = []
    findings.append(_run_e1(business_id, overlay, app_view))
    findings.append(_run_llm_check("E2", app_view))
    findings.append(_run_e3(business_id, app_view))
    if "eidl" in program:
        findings.append(_run_llm_check("E4", app_view))
    findings.append(_run_e5(overlay, app_view))
    findings.append(_run_e6(business_id, app_view))
    return findings


def _run_e1(business_id: str, overlay: dict, app_view: dict) -> Finding:
    """Mostly deterministic: compare against the parsed §121.201 standard."""
    sections = _sections_for("E1")
    naics = overlay.get("naics_code")
    std = size_standards().get(naics)
    revenue = loaders.fs_annual_revenue(business_id)
    employees = overlay.get("employee_count")

    if std is None:
        verdict, confidence = "flag", 0.4
        rationale = (
            f"No §121.201 size standard found for NAICS {naics}; cannot verify size "
            "deterministically — needs human review."
        )
        cited = "The size standards described in this section apply to all SBA programs."
    else:
        if std["type"] == "employees":
            over = employees is not None and employees > std["threshold"]
            measure = f"{employees} employees vs. limit {int(std['threshold'])}"
        else:  # receipts, threshold in $millions
            rev_m = (revenue or 0) / 1_000_000
            over = rev_m > std["threshold"]
            measure = f"${rev_m:.2f}M receipts vs. limit ${std['threshold']}M"
        verdict = "fail" if over else "pass"
        confidence = 0.95
        rationale = (
            f"{overlay.get('business_name')} ({std['title']}, NAICS {naics}): {measure}. "
            + ("Exceeds the size standard — not a small business." if over else "Within the size standard.")
        )
        cited = f"{std['title']}: size standard {std['threshold']}"
        if std["type"] == "receipts":
            cited += " million dollars in average annual receipts."
        else:
            cited += " employees."

    return Finding(
        check_id="E1",
        subject="application",
        verdict=verdict,
        confidence=confidence,
        citation="13 CFR § 121.201",
        cited_text=cited,
        rationale=rationale,
    )


def _run_e5(overlay: dict, app_view: dict) -> Finding:
    """Deterministic presence check on certification booleans."""
    sections = _sections_for("E5")
    certs = overlay.get("certifications", {})
    missing = [k for k in ("nondiscrimination", "anti_lobbying") if not certs.get(k)]
    if missing:
        verdict, confidence = "fail", 0.9
        rationale = f"Missing required certification(s): {', '.join(missing)}."
    else:
        verdict, confidence = "pass", 0.9
        rationale = "Required certifications (nondiscrimination, anti-lobbying) are present."
    cited = sections[0]["heading"] if sections else "Certification requirements."
    return Finding(
        check_id="E5",
        subject="application",
        verdict=verdict,
        confidence=confidence,
        citation="13 CFR § 146.100" if sections else "13 CFR Parts 112/146",
        cited_text=cited,
        rationale=rationale,
    )


def _run_e3(business_id: str, app_view: dict) -> Finding:
    sections = _sections_for("E3")
    uop = loaders.fs_use_of_proceeds(business_id)
    extra = f"\nUSE OF PROCEEDS LINE ITEMS:\n{uop}"
    prompt = prompts.eligibility_user_prompt(
        "E3", CHECK_DESCRIPTIONS["E3"], app_view, sections, extra
    )
    judgment = judge(prompts.SYSTEM_PROMPT, prompt, JUDGMENT_JSON_SCHEMA)
    return _finding("E3", judgment, sections)


def _run_e6(business_id: str, app_view: dict) -> Finding:
    sections = _sections_for("E6")
    package = loaders.fs_package_safe(business_id)
    extra = f"\nFINANCIAL PACKAGE (all line items):\n{package}"
    prompt = prompts.eligibility_user_prompt(
        "E6", CHECK_DESCRIPTIONS["E6"], app_view, sections, extra
    )
    judgment = judge(prompts.SYSTEM_PROMPT, prompt, JUDGMENT_JSON_SCHEMA)
    return _finding("E6", judgment, sections)


def _run_llm_check(check_id: str, app_view: dict) -> Finding:
    sections = _sections_for(check_id)
    prompt = prompts.eligibility_user_prompt(
        check_id, CHECK_DESCRIPTIONS[check_id], app_view, sections
    )
    judgment = judge(prompts.SYSTEM_PROMPT, prompt, JUDGMENT_JSON_SCHEMA)
    return _finding(check_id, judgment, sections)
