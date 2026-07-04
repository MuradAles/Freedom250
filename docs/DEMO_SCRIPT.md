# Demo Script — what I say (SBA Loan Compliance Checker)

_First person. Read it, then make it yours. ~4 minutes. Cut the "under the hood"
section first if I'm short on time._

---

## OPEN (say this while the dashboard is on screen)

"Hi everyone. So — back in 2020, the government pushed trillions of dollars out
the door through PPP and EIDL loans, and they did it fast. The hard part came
afterward. You've got thousands of borrowers, thousands of transactions, and
one three-megabyte book of federal regulations you're supposed to check all of
that against.

Right now, a human reviewer does this by hand. It's slow, it's inconsistent
from person to person, and — this is the real problem — they can almost never
point to the exact rule their decision is based on.

So we built a tool that does that review in seconds. And more importantly, it
cites the exact regulation behind every single decision."

---

## WHAT IT IS (say this next, still on the dashboard)

"What you're looking at is an SBA loan compliance checker. It takes a loan
record and validates it against the real federal regulations — Title 13 of the
Code of Federal Regulations — and it gives a loan officer a clear pass, flag, or
fail on every requirement.

And notice this stat at the top — 'LLM versus ground truth, 48 of 60 agree.'
That matters. We don't just ask you to trust it. We tested it against data that
was already labeled by humans, and it agreed 80% of the time. So we measured
ourselves."

---

## LIVE DEMO — ELIGIBILITY (click into an application, then "Run checks")

"Let me show you. I'll pick a loan application and run the checks.

[click Run checks]

Here we go. Six eligibility checks, and each one maps to a specific regulation.
Is the business small enough? Is it an eligible type of business? Are they
spending the money on allowed things? Are the required certifications there? Can
they actually repay?

Every one comes back with a verdict, a confidence level, and a plain-language
rationale — something a loan officer can actually read and act on."

---

## LIVE DEMO — THE CITATION DRAWER (click a citation — this is the key moment)

"Now here's the part I really want you to see.

[click a citation link]

Every citation is clickable. And when I click it — it opens the actual,
word-for-word federal regulation the decision was based on.

This is what makes it defensible. The tool isn't guessing, and it isn't making
up law. It's pointing straight at the regulation. If an auditor asks 'why did
you approve this?' — the answer is right here."

---

## LIVE DEMO — TRANSACTION AUDIT (back out, pick a flagged business, "Run audit")

"That was the before — checking if a loan should be approved. This is the after.

[click Run audit]

Once the money's been spent, we audit the transactions. The tool judges each one
and rolls it up into a risk verdict — clear, needs review, or possible fraud.

Legitimate spending — payroll, rent, utilities — passes. But an owner using loan
money to buy a personal car, or transfer cash to themselves? That gets flagged
or failed. And again — it cites the rule it's based on."

---

## UNDER THE HOOD (optional — cut this first if short on time)

"Two quick things we're proud of technically.

One — the regulation text is real. We indexed all fifteen hundred sections of
Title 13, and every check pulls the exact section by its citation. So the
results are reproducible, and the tool can't hallucinate a rule that doesn't
exist.

Two — the confidence scores aren't just the AI's gut feeling. They follow a
defined rubric, and anything the tool isn't sure about automatically gets
escalated to a human. It does the fast first pass — but it never fakes
certainty."

---

## CLOSE

"So that's it. Regulatory review that used to take hours — done in seconds. And
every decision cites the exact law behind it.

That's the whole point: the difference between an answer, and an answer you can
actually defend. Thanks — happy to take questions."

---

## Q&A — likely questions and my answers

**"Is this using RAG / how does it find the right regulation?"**
"We don't search for it — each check maps to a known regulation and fetches that
exact section. It's reproducible and it can't grab the wrong one. Semantic search
is our future-work path, for open-ended questions."

**"How is the confidence number calculated?"**
"For the math-based checks like size, we compute it from the data — how far the
business is from the limit. For the judgment checks, the AI scores against a
defined rubric. And anything under 60% automatically becomes a flag for human
review. It's a consistent, explainable score — not a statistical probability,
and we're upfront about that."

**"Is the data real?"**
"The regulations are 100% real — that's the actual Title 13. The loan records
and transactions are synthetic fixtures, so we can demo without exposing real
borrower data."

**"What happens when it's wrong?"**
"That's exactly why 'flag' exists. Low confidence or anything ambiguous doesn't
get a silent pass or fail — it routes to a human. The tool is an assistant, not
the final decision-maker."

---

## Delivery reminders
- Lead with the scale: "trillions, thousands of transactions, one 3MB rulebook."
- SLOW DOWN on the citation drawer click — say "this is what makes it defensible."
- Say the 80% number out loud — most demos never show they measured themselves.
- If a citation looks repetitive on the audit table, don't dwell: "per-transaction
  citations are a refinement we're polishing." Move on.
