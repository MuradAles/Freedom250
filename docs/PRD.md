# PRD: SBA Loan Compliance Checker

**Status:** Draft v1 · **Scope:** Hackathon/demo MVP · **Repo:** Freedom250

## 1. Overview

A review tool for SBA loan officers/auditors that validates loan records against Title 13 CFR Chapter I (the actual SBA regulations). It performs two kinds of checks:

1. **Application eligibility check (pre-approval)** — is this applicant/loan eligible under Title 13?
2. **Use-of-proceeds audit (post-disbursement)** — did the borrower spend the funds on eligible costs?

All data is mocked **except the regulations themselves** — the Title 13 text is real, and the LLM validation pipeline against it is the core of the product. Everything else (applications, transactions, users) is synthetic fixture data.

## 2. Problem

SBA lenders and reviewers must verify applications and fund usage against dense federal regulations (13 CFR). Manual review is slow, inconsistent, and rarely cites the specific regulation a decision rests on. Post-2020 (PPP/EIDL), fraud and misuse audits made this worse: thousands of borrowers, thousands of transactions, one 3MB regulation.

## 3. User

**Primary persona: SBA reviewer / lender loan officer.** Reviews a queue of loan records, needs a fast pass/flag/fail signal per requirement, and — critically — a **citation to the exact Title 13 section** behind every finding so the decision is defensible.

Not in scope as personas: borrowers self-checking, SBA policy staff.

## 4. Goals & Non-Goals

### Goals
- One happy path working end to end: pick a loan record → see eligibility findings → see flagged transactions → drill into the regulation text behind each finding.
- Every finding carries a **specific CFR citation** (e.g., § 120.110(b)) and plain-language rationale.
- Demonstrate a real retrieval + LLM-judge pipeline over the actual Title 13 text.
- Use the labeled transaction CSV as a built-in eval: report LLM agreement vs. ground-truth labels.

### Non-Goals (explicitly out of scope for V1)
- Authentication / user management
- Real document upload, OCR, or bank-feed ingestion
- PPP forgiveness calculations
- Substantive checks against Title 13 parts beyond 120, 121, and 123 (the full chapter **is** indexed for citation lookup and fallback search, and certifications tied to Parts 112/113/146 get a presence check — but no other part yields a data-validatable rule; see Appendix A)
- Persistence (no database — files loaded in memory)
- Production hardening (rate limiting, audit trails, PII handling)

## 5. Data

### 5.1 Regulations (real)
`data/title-13-chapter-i.txt` (~3MB, in `data/` on main). **The entire chapter (all 34 parts) is preprocessed** at startup (or build time) into section-level chunks keyed by citation (`part`, `section`, `heading`, `text`). Indexing everything is nearly free and buys two things: the citation drawer can resolve *any* citation the judge produces, and the keyword fallback can reach sections outside the curated set instead of leaving the judge to hallucinate.

The **curated checks**, however, scope to the three parts that contain applicant-checkable rules (rationale for every other part in Appendix A):

| Part | Why |
|---|---|
| 120 | Business loan programs — eligibility (§120.100–.111), eligible/prohibited use of proceeds (§120.120, §120.130, §120.160) |
| 121 | Small business size standards (§121.201 table, by NAICS code) |
| 123 | Disaster loans (EIDL) — eligibility and use restrictions |
| 112/113, 146 | Certification requirements only (nondiscrimination assurances, anti-lobbying) — presence-checked, not judged (see E5) |

### 5.2 Application-side: financial submission packages (existing mock)
`data/financial_submission_examples/` — 4 businesses (`FS001–FS004`), each a full application-time financial package as row-per-line-item CSV: profit & loss, balance sheet, bank statement activity, AR/AP aging, debt schedule, inventory report, projected cash flow, and **use-of-proceeds line items** (with amounts and source documents). Each package carries a ground-truth `case_label` (`normal` / `needs_review`) and per-row `review_note`s. This is the primary data behind the **application review** surface — the use-of-proceeds rows feed E3 directly, and the package as a whole feeds the financial-review check (E6).

### 5.3 Audit-side: per-business transaction files (existing mock)
`data/sba_business_examples/` — 8 businesses (`BIZ001–BIZ008`, PPP/EIDL) with per-transaction rows (`category`, `merchant`, `description`, `owner_related`, `documentation`) plus ground truth at **two levels**: per-transaction `transaction_label` (`allowed`/`inappropriate`/questionable) and per-business `case_label` (`not_flagged` / `needs_review` / `flagged_possible_fraud`). Primary data behind the **transaction audit** surface. All `label`/`case_label`/`review_note`/`risk_score` columns are **hidden from the LLM** and used only for evals (§8).

### 5.4 Eval-only sets
- `data/synthetic_sba_audit_transactions.csv` — original 60 labeled transactions (`B001–B012`); transaction-level eval corpus.
- `data/sba_transactions_{not_flagged,flagged,mixed_review}.csv` — pooled labeled transactions grouped by outcome (`NF*/FG*/MX*` ids).
- `data/sba_borrower_risk_summary.csv` — borrower-level aggregates with ground-truth `audit_flag`/`label`; enables a borrower-level eval on top of the transaction-level one.

### 5.5 Application metadata overlay (new mock — to be created)
The financial packages don't carry NAICS code, employee count, ownership, or certifications — the fields E1, E2, and E5 need. `data/mock_applications.json` supplies them as a thin overlay keyed by `business_id` (covering the FS and BIZ businesses):

```json
{
  "business_id": "FS001",
  "business_name": "Main Street Bakery",
  "program": "7a",                        // matches submission_context / program in the CSVs
  "loan_amount": 185000,
  "naics_code": "311811",
  "business_type": "LLC",
  "employee_count": 12,
  "years_in_business": 6,
  "ownership": [{ "name": "Jane Doe", "pct": 100 }],
  "certifications": { "nondiscrimination": true, "anti_lobbying": true }
}
```

(Annual revenue comes from the package's P&L, intended use from its use-of-proceeds rows — no duplication.) Overlay values are designed so specific checks trip on specific records — at minimum: one fully clean, one over the Part 121 size standard for its NAICS code, one ineligible business type under §120.110 (e.g., lending, gambling, speculation), and one missing a required certification (E5). Prohibited-use (§120.130) and EIDL (Part 123) cases already exist in the packages/transactions.

> **ID namespaces:** the datasets use disjoint id schemes (`FS*`, `BIZ*`, `B0*`, `NF*/FG*/MX*`). The overlay file is the join point; there is no cross-dataset linking beyond it. FS businesses get the application tabs, BIZ businesses get the transaction tab.

## 6. Functional Requirements

### 6.1 Eligibility check (application)
For a selected application, run these checks. Each check = retrieve candidate Title 13 sections → LLM judges → structured finding.

| # | Check | Regulation anchor |
|---|---|---|
| E1 | Business size vs. size standard for NAICS code | Part 121 (§121.201) |
| E2 | Eligible business type / not an ineligible business | §120.110 |
| E3 | Intended use of proceeds is eligible (judges the package's use-of-proceeds line items) | §120.120, §120.130 |
| E4 | (EIDL only) disaster loan eligibility & use rules | Part 123 |
| E5 | Required certifications present (nondiscrimination assurance, anti-lobbying) | Parts 112/113, 146 |
| E6 | Financial package review — repayment ability, documentation completeness, internal consistency (P&L vs. bank activity, debt load, cash position) | §120.150 (SBA lending criteria), §120.160 |

E1 and E5 are (mostly) deterministic — E1 compares against the parsed §121.201 size table, E5 checks booleans on the application; the LLM only writes the citation-backed rationale. E2–E4 and E6 are full LLM judgments. Certifications are attestations ("I certify I will comply"), so E5 can only verify they were made, not that they're true — that's faithful to how real SBA review treats them. E6 consumes the whole financial package (§5.2) and is where the `normal`/`needs_review` ground truth gives us a second eval; its verdicts skew toward `flag` rather than `fail` since credit weakness is a judgment call, not a violation.

### 6.2 Use-of-proceeds audit (transactions)
For each transaction of a selected business (`sba_business_examples/`), judge whether the spend is an eligible use under the business's program, producing the same finding shape — plus one **business-level rollup verdict** (clear / needs review / possible fraud) derived from the per-transaction findings, mirroring the dataset's business-level `case_label`. Transactions are batched per business into one LLM call to keep the demo fast/cheap.

### 6.3 Finding shape (both surfaces)
```json
{
  "check_id": "E2",
  "subject": "application" ,             // or "transaction:<row>"
  "verdict": "pass",                     // pass | flag | fail
  "confidence": 0.86,
  "citation": "13 CFR § 120.110(b)",
  "cited_text": "…verbatim excerpt relied on…",
  "rationale": "Plain-language explanation a loan officer can read."
}
```
`flag` = needs human review (ambiguous or low confidence); `fail` = clear violation. Verdicts are produced via structured outputs so the response is always parseable.

### 6.4 UI (React, existing CRA app)
1. **Dashboard** — table of loan records: business name, program, amount, overall status chip (Clear / Flagged / Failed / Not run), "Run checks" action.
2. **Detail view** — two tabs (Eligibility for FS businesses, Transactions for BIZ businesses; both shown when data exists):
   - *Eligibility*: one card per check (E1–E4) showing verdict, citation, rationale.
   - *Transactions*: transaction table with verdict badges; flagged/failed rows highlighted.
3. **Citation drawer** — clicking any citation opens the verbatim Title 13 excerpt the judge relied on. Backed by the full-chapter index, so any section in Chapter I resolves, not just the curated ones.
4. **Eval widget** (nice-to-have) — small "LLM vs. ground truth: N/60 agree" stat sourced from the CSV labels.

### 6.5 API (FastAPI, existing backend)
| Endpoint | Purpose |
|---|---|
| `GET /api/applications` | List mock applications with last-run status |
| `GET /api/applications/{borrower_id}` | Application + transactions + cached findings |
| `POST /api/applications/{borrower_id}/check` | Run eligibility checks, return findings |
| `POST /api/applications/{borrower_id}/audit` | Run transaction audit, return findings |
| `GET /api/regulations/{citation}` | Return section text for the citation drawer |
| `GET /api/eval` | Agreement stats vs. CSV ground truth |

Findings are cached in memory per borrower so re-visiting a record doesn't re-spend tokens.

## 7. Validation Pipeline (the real part)

```
Title 13 txt ──(startup)──> section chunks {citation, heading, text}
                                   │
check request ──> retrieval: per-check curated section map + keyword match
                                   │  (E1→§121.201, E2→§120.110, E3→§120.120/.130, E4→Part 123)
                                   ▼
              LLM judge (Claude) — system: reviewer role + finding schema
              user: application/transaction JSON + retrieved section texts
                                   ▼
              structured findings (JSON) ──> API ──> UI
```

Design decisions:
- **Retrieval is a lookup, not RAG.** The checks are fixed and each maps to a known list of sections, so "retrieval" is a static `check_id → [citations]` map over the section index — the same check always sees the same regulation text (reproducible findings, no retrieval misses, no vector DB/embedding infrastructure). Keyword search over the chunk index is the fallback for edge cases. Real RAG only becomes necessary if we later support free-form questions or checks whose relevant sections aren't known in advance — noted as future work, not V1.
- **Index the whole chapter, scope the checks.** All 34 parts are chunked and indexed even though curated checks only cite Parts 120/121/123 (+ certification parts). This lets the citation drawer resolve any citation and gives the keyword fallback full coverage, at negligible cost.
- **LLM:** Anthropic API, `claude-opus-4-8`, adaptive thinking, **structured outputs** (`output_config.format` with the finding JSON schema) so verdicts always parse. Retrieved regulation text goes in a cached prompt block (`cache_control`) since it repeats across checks.
- **Ground-truth isolation:** the judge never sees `label`/`label_reason`; those columns are stripped before prompting.

## 8. Eval (built-in)

The labeled datasets give three eval levels, all with ground truth hidden from the judge:

1. **Transaction-level** — run the audit over the original 60-row CSV plus the pooled `sba_transactions_*.csv` sets; compare verdicts to `label` (`allowed`→pass, `inappropriate`→fail, `questionable`→flag).
2. **Business-level** — compare the rollup verdict (§6.2) to `case_label` in `sba_business_examples/` and `audit_flag` in `sba_borrower_risk_summary.csv`.
3. **Submission-level** — compare E6's verdict to the `case_label` (`normal`/`needs_review`) on the four financial packages.

Output per level: agreement %, confusion counts, and the disagreeing rows for inspection. This is both a demo talking point and a regression check while iterating on prompts.

## 9. Success Criteria

- Demo flow (§6.4) works end to end on first click for all mock applications.
- 100% of findings include a resolvable citation (the drawer can display the cited section).
- ≥ 85% agreement with CSV ground truth on the transaction audit.
- A full check run for one borrower completes in < 30s.

## 10. Milestones

1. **Data** — Title 13 chunker + citation index; loaders for the submission packages and business examples; author the `mock_applications.json` overlay. (All source datasets already live in `data/` on main.)
2. **Pipeline** — judge prompt + structured output schema; eligibility checks E1–E3 + E5; transaction audit + business rollup; transaction-level eval.
3. **API** — FastAPI endpoints (§6.5) with in-memory caching.
4. **UI** — dashboard, detail tabs, citation drawer.
5. **Polish** — E4 (EIDL/Part 123), E6 (financial package review) + submission-level eval, eval widget, demo script.

## 11. Open Questions

- §121.201 size standards are a large table — parse it into structured `{naics → threshold}` data, or let the LLM read the raw table text? (Lean: parse; it makes E1 mostly deterministic.)
- PPP is governed largely by the CARES Act, not Title 13 alone — for the demo we judge PPP use-of-proceeds against §120.120/.130 general rules plus the categories in the CSV; acceptable for a mock, flag if fidelity matters later.
- Should `flag` verdicts support a human override (mark reviewed) in V1, or is read-only enough for the demo?

## Appendix A — Why checks scope to Parts 120/121/123

Title 13 CFR Chapter I contains 34 parts, but the test for inclusion as a *check* is: **does this part impose a requirement on the applicant/borrower that can be validated against application or transaction data?** Only three parts pass; the rest fall into four buckets:

| Bucket | Parts | Why not checkable |
|---|---|---|
| **Applicant-checkable loan rules** ✅ | **120** (Business Loans), **121** (Size Regulations), **123** (Disaster Loans) | These are the checks. Matches how SBA reviewers actually work — 7(a) eligibility review is essentially a walk through Parts 120 and 121. |
| Different SBA programs | 107 (SBICs), 108 (NMVC), 109 (lending pilot), 115 (surety bonds), 119 (PRIME), 124 (8(a)), 125–129 (contracting programs: HUBZone, WOSB, veteran cert), 130–131 (development centers) | Contain eligibility rules, but for investment/grant/contracting programs — a 7(a)/PPP/EIDL application is not subject to them. Citing them against a loan would be a category error. |
| SBA/lender conduct | 101 (administration), 102 (FOIA/privacy), 103 (agents & packager fees), 105–106 (employee conduct, gifts), 114 (tort claims), 134 (appeals procedure), 140 (debt collection), 142 (false claims procedure) | Obligations fall on the agency or lender, not the applicant. (Part 105's SBA-employee conflict rule is the one edge case; the applicant-side version is already covered by §120.110's ineligible list.) |
| Certification-only | 112/113/117/136 (nondiscrimination), 146 (lobbying), 147 (drug-free workplace) | Appear on a real application only as signed attestations ("I certify I will comply") — no business data can prove or disprove them. E5 checks that they're *present*, which is all a human reviewer does too. |
| Reserved/empty | 1–100, 143, 148–199 | No content. |

Possible future additions: certification-claim verification (Parts 124/126/127/128) if applications ever assert 8(a)/HUBZone/WOSB/veteran status — nothing in the current mock data does.
