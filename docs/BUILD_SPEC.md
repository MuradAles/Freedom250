# BUILD SPEC — SBA Loan Compliance Checker

This is the **single source of truth** for the build. It derives from `docs/PRD.md` and
locks the API contract, data shapes, and module layout so the backend and frontend can be
built in parallel without drift. If something here conflicts with the PRD, the PRD wins —
raise it, don't silently diverge.

## 0. Stack & conventions
- **Backend:** FastAPI + `uv`, Python 3.11+. LLM via **OpenRouter** (OpenAI-compatible API)
  using the `openai` SDK pointed at `settings.openrouter_base_url`. Model from `settings.llm_model`
  (default `anthropic/claude-sonnet-4.5`).
- **Frontend:** existing CRA app (React 19), `frontend/`. Plain fetch, no new heavy deps
  (no redux/router required; a single-file router by state is fine). Dev proxy: add
  `"proxy": "http://localhost:8000"` to `frontend/package.json` so the UI can call `/api/...`.
- **Config:** already wired in `backend/app/config.py` — read `settings.openrouter_api_key`,
  `settings.llm_model`, `settings.data_dir`. Never hardcode keys.
- **No DB.** Everything loads into memory at startup. Findings cached in a module-level dict.
- **Ground-truth isolation (critical):** columns `case_label`, `transaction_label`, `label`,
  `label_reason`, `review_note`, `risk_score`, `audit_flag` are **NEVER** sent to the LLM.
  They exist only for the eval module. Enforce this in the loaders/serializers.

## 1. Data facts (verified against the repo)
- `data/title-13-chapter-i.txt` (~3MB). Sections marked `Sec. <part>.<num> <heading>` at line
  start, e.g. `Sec. 120.110 What businesses are ineligible...`. Parts marked `PART <n>--<TITLE>`.
  ~1519 `Sec.` lines. Section body = text between one `Sec.` line and the next `Sec.`/`PART`.
- `§121.201` (line ~14271) is a huge inline NAICS table: tokens like `311811 Retail Bakeries $X`
  (annual-receipts, millions) or `... <heading> <NNN>` (employee count). Parse into
  `{naics_code: {"type": "receipts"|"employees", "threshold": float, "title": str}}`.
  Threshold in **millions of dollars** for receipts, **count** for employees. Best-effort
  regex parsing is fine; log how many codes parsed.
- `data/financial_submission_examples/` — `index.csv` (FS001–FS004: business_id, business_name,
  submission_context, requested_amount, case_label, contents) + one row-per-line-item CSV each.
  Row cols: `business_id,business_name,submission_context,period,record_type,record_date,
  line_item,counterparty,description,debit,credit,balance,amount,source_document,case_label,review_note`.
  `record_type` ∈ {profit_and_loss, balance_sheet, bank_statement, ar_aging, ap_aging,
  projected_cash_flow, use_of_proceeds, ...}. **use_of_proceeds rows feed E3.**
- `data/sba_business_examples/` — `index.csv` (BIZ001–BIZ008: business_id, business_name,
  program, loan_amount, case_label, summary) + one CSV each. Transaction cols:
  `...,transaction_date,amount,category,merchant,description,owner_related,documentation,
  risk_score,transaction_label,label_reason`. **These feed the transaction audit.**
- Eval-only: `data/synthetic_sba_audit_transactions.csv` (B001–B012, col `label`),
  `data/sba_transactions_{not_flagged,flagged,mixed_review}.csv` (NF*/FG*/MX*, col `label`),
  `data/sba_borrower_risk_summary.csv` (NF*/FG*/MX* aggregates, col `audit_flag`,`label`).
- **`data/mock_applications.json`** — to be authored by the data-layer builder (see §4).

## 2. Finding schema (shared, exact)
```json
{
  "check_id": "E2",
  "subject": "application",
  "verdict": "pass",
  "confidence": 0.86,
  "citation": "13 CFR § 120.110(b)",
  "cited_text": "…verbatim excerpt relied on…",
  "rationale": "Plain-language explanation a loan officer can read."
}
```
- `verdict` ∈ `"pass" | "flag" | "fail"`. `subject` = `"application"` or `"transaction:<row_index>"`.
- `confidence` ∈ [0,1]. `citation` is a human string; `cited_text` is verbatim from Title 13.
- Produced via **structured output** (OpenAI-compatible `response_format` JSON schema) so it
  always parses. If the SDK/model rejects strict schema, fall back to
  `response_format={"type":"json_object"}` + prompt instruction + `json.loads` with a repair retry.

## 3. API contract (frozen — frontend builds against this)
Base path `/api`. All responses JSON. On LLM/key failure, return HTTP 200 with findings that
carry `verdict:"flag"` and a rationale explaining the pipeline error IS acceptable for the demo,
**except** `/check` and `/audit` may return 503 with `{"detail": "..."}` if the API key is missing —
frontend must show a friendly "LLM not configured" state.

### `GET /api/applications`
Returns the dashboard list.
```json
[
  {
    "borrower_id": "FS001",
    "business_name": "Main Street Bakery",
    "program": "7a",
    "amount": 185000,
    "kind": "application",          // "application" (FS*) | "audit" (BIZ*)
    "status": "not_run"            // not_run | clear | flagged | failed
  }
]
```
- `status` reflects cached findings: `failed` if any fail, else `flagged` if any flag, else
  `clear` if findings exist, else `not_run`. `kind` decides which tab(s) the detail view shows.

### `GET /api/applications/{borrower_id}`
```json
{
  "borrower_id": "FS001",
  "business_name": "...",
  "program": "7a",
  "amount": 185000,
  "kind": "application",
  "application": { ...overlay fields + derived annual_revenue + use_of_proceeds[] ... } | null,
  "transactions": [ { "row": 0, "date": "...", "amount": 1234.0, "category": "...",
                      "merchant": "...", "description": "...", "owner_related": false,
                      "documentation": "..." } ] | null,   // BIZ only, GROUND TRUTH STRIPPED
  "eligibility_findings": [ Finding, ... ],   // cached; [] if not run
  "audit_findings": [ Finding, ... ],         // cached; [] if not run
  "rollup": { "verdict": "clear|needs_review|possible_fraud", "rationale": "..." } | null
}
```

### `POST /api/applications/{borrower_id}/check`
Runs eligibility E1–E6 for an FS* application. Returns
`{ "eligibility_findings": [Finding, ...] }`. Caches result.

### `POST /api/applications/{borrower_id}/audit`
Runs transaction audit for a BIZ* business (one batched LLM call). Returns
`{ "audit_findings": [Finding, ...], "rollup": { "verdict": ..., "rationale": ... } }`. Caches.

### `GET /api/regulations/{citation}`
`citation` is URL-encoded (e.g. `13%20CFR%20%C2%A7%20120.110` or just `120.110`). Resolver must
normalize: strip `13 CFR`, `§`, `Sec.`, subsection parens `(b)`, whitespace → match on
`part.section`. Returns `{ "citation": "...", "part": "120", "section": "120.110",
"heading": "...", "text": "..." }` or 404 `{"detail":"citation not found"}`.

### `GET /api/eval`
```json
{
  "transaction_level": { "total": 60, "agree": 54, "agreement_pct": 90.0,
                          "confusion": {"pass":{"pass":..}, ...},
                          "disagreements": [ {"id":"B003","expected":"fail","got":"flag", ...} ] },
  "business_level":   { ...same shape over rollups vs case_label/audit_flag... },
  "submission_level": { ...E6 verdict vs FS case_label (normal->pass, needs_review->flag)... }
}
```
Label→verdict mapping: `allowed→pass`, `inappropriate→fail`, `questionable→flag`;
`normal→pass`, `needs_review→flag`; business `not_flagged→clear`, `needs_review→needs_review`,
`flagged_possible_fraud→possible_fraud`. The eval endpoint may run a **capped** subset by
default (e.g. `?limit=N`) to keep cost bounded, but must support the full 60-row run.

## 4. `data/mock_applications.json` (data-layer builder authors this)
Array of overlay objects keyed by `business_id`, covering **all FS and BIZ** businesses. Shape:
```json
{
  "business_id": "FS001", "business_name": "Main Street Bakery",
  "program": "7a", "loan_amount": 185000, "naics_code": "311811",
  "business_type": "LLC", "employee_count": 12, "years_in_business": 6,
  "ownership": [{ "name": "Jane Doe", "pct": 100 }],
  "certifications": { "nondiscrimination": true, "anti_lobbying": true }
}
```
Design the FS overlay values so checks trip predictably (PRD §5.5):
- **FS001** fully clean (retail bakery NAICS 311811, small, all certs).
- One FS **over the Part 121 size standard** for its NAICS (pick a NAICS with a low receipts or
  employee threshold and set revenue/employees above it).
- One FS **ineligible business type under §120.110** (e.g. NAICS for a lending / gambling /
  speculation business).
- One FS **missing a required certification** (`nondiscrimination:false` or `anti_lobbying:false`).
- FS003 is EIDL (`submission_context` sba_eidl...) → exercises E4.
- Map `program` sensibly: FS `submission_context` → `7a`/`eidl`/`term_loan`; BIZ already has `program`.
- `naics_code` must be a code that actually appears in the parsed §121.201 table so E1 resolves.
BIZ overlay entries can be lighter (program/naics/type) since BIZ uses the audit path, but include
them so `GET /api/applications` can show program/amount.

## 5. Backend module layout (target)
```
backend/app/
  config.py                 # DONE (orchestrator)
  main.py                   # include routers, load data at startup
  data/
    regulations.py          # Title 13 chunker + citation index + resolver + keyword search
    size_standards.py       # §121.201 NAICS table parser -> {naics: {...}}
    loaders.py              # FS packages, BIZ transactions, eval CSVs, mock_applications.json
    __init__.py
  llm/
    client.py               # OpenRouter client wrapper + structured-output call
    prompts.py              # judge system prompt + per-check user-prompt builders
    schema.py               # Finding pydantic model + JSON schema for structured output
    checks.py               # E1..E6 eligibility, retrieval map check_id->[citations]
    audit.py                # transaction audit (batched) + business rollup
    eval.py                 # transaction/business/submission agreement stats
    __init__.py
  routers/
    health.py               # DONE
    applications.py          # GET list, GET one, POST check, POST audit
    regulations.py           # GET /api/regulations/{citation}
    evals.py                 # GET /api/eval
  store.py                  # in-memory findings cache + loaded data singletons
```
Retrieval map (static, PRD §7): `E1→[121.201]`, `E2→[120.110]`, `E3→[120.120,120.130]`,
`E4→[123.200,123.201,123.300,123.303]`, `E5→[112, 146]` (presence-check; citation for rationale),
`E6→[120.150,120.160]`. Audit → `[120.120,120.130]` (+123 for EIDL programs).

## 6. Frontend layout (target)
```
frontend/src/
  App.js                    # state router: dashboard <-> detail; holds citation drawer
  api.js                    # fetch helpers for all endpoints
  components/
    Dashboard.jsx           # table: name, program, amount, status chip, Run checks btn
    DetailView.jsx          # header + tabs (Eligibility | Transactions per kind)
    FindingCard.jsx         # verdict badge, citation (clickable), rationale
    TransactionsTable.jsx   # rows w/ verdict badges, flagged/failed highlight, rollup banner
    CitationDrawer.jsx      # slide-over; fetches /api/regulations/{citation}
    EvalWidget.jsx          # "LLM vs truth: N/60 agree" from /api/eval
    StatusChip.jsx, VerdictBadge.jsx
  styles.css                # simple, clean, professional (loan-officer tool)
```
Behavior: Dashboard "Run checks" calls `/check` (FS) or `/audit` (BIZ), then refreshes detail.
Any citation string anywhere is clickable → opens drawer. Eval widget on dashboard.
Loading + error states required (esp. the "LLM not configured" 503).

## 7. Definition of done
- `uv run pytest` green; `uv run uvicorn app.main:app` boots and loads data without error.
- All §3 endpoints respond with the documented shapes (verifiable without an API key for GET
  list/detail/regulations; `/check` `/audit` `/eval` need the key).
- 100% of findings carry a citation the `/api/regulations` resolver can resolve.
- Frontend builds (`npm run build`) and the demo flow works end to end.
- No ground-truth column ever appears in an LLM prompt (grep the prompt builders).
