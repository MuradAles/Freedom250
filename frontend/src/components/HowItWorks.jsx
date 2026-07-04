import React, { useState } from 'react';

/** Collapsible explainer panel: what the tool checks, how verdicts are graded,
 * and what the confidence score means. Sits at the top of the dashboard so the
 * review model is self-documenting during a demo. */
export default function HowItWorks() {
  const [open, setOpen] = useState(true);

  return (
    <section className="explainer">
      <button
        className="explainer-toggle"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span>How this works</span>
        <span className="explainer-chevron">{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="explainer-body">
          <p className="explainer-intro">
            This tool reviews SBA loan records against the real federal
            regulations (Title&nbsp;13&nbsp;CFR) and returns a defensible finding —
            a verdict, the exact regulation it cites, and how confident it is.
          </p>

          <div className="explainer-grid">
            {/* What we check */}
            <div className="explainer-col">
              <h4>What we check</h4>
              <p className="explainer-sub">
                <strong>Eligibility</strong> (should this loan be approved?) — six
                checks, each mapped to a specific regulation:
              </p>
              <ul className="explainer-list">
                <li><b>E1</b> — Business size vs. its industry standard (§121.201)</li>
                <li><b>E2</b> — Eligible business type (§120.110)</li>
                <li><b>E3</b> — Eligible use of proceeds (§120.120/.130)</li>
                <li><b>E4</b> — Disaster/EIDL rules (Part 123)</li>
                <li><b>E5</b> — Required certifications present</li>
                <li><b>E6</b> — Financial strength & repayment (§120.150/.160)</li>
              </ul>
              <p className="explainer-sub">
                <strong>Audit</strong> (was the money spent properly?) — judges each
                transaction, then rolls up to a fraud-risk verdict.
              </p>
            </div>

            {/* Verdicts */}
            <div className="explainer-col">
              <h4>How it's graded</h4>
              <ul className="explainer-verdicts">
                <li>
                  <span className="chip chip-green">Pass</span>
                  Clearly compliant with the cited regulation.
                </li>
                <li>
                  <span className="chip chip-amber">Flag</span>
                  Ambiguous or low-confidence — a human should review.
                </li>
                <li>
                  <span className="chip chip-red">Fail</span>
                  A clear violation of the cited regulation.
                </li>
              </ul>
              <p className="explainer-note">
                Every finding links to the <b>verbatim regulation text</b> it relied
                on — click any citation to see it.
              </p>
            </div>

            {/* Confidence */}
            <div className="explainer-col">
              <h4>How confidence is scored</h4>
              <p className="explainer-sub">
                Not a guess — a rubric-anchored score in&nbsp;0–1:
              </p>
              <ul className="explainer-bands">
                <li><b>0.90–1.00</b> — regulation names this exact case</li>
                <li><b>0.75–0.89</b> — clear rule, one inference step</li>
                <li><b>0.60–0.74</b> — general principle or partial data</li>
                <li><b>0.40–0.59</b> — weak signal, missing info</li>
                <li><b>below 0.40</b> — no basis to decide</li>
              </ul>
              <p className="explainer-note">
                Size &amp; certification checks compute confidence from the data
                (e.g. distance from the size limit). Anything under <b>0.60</b>{' '}
                auto-escalates to a <span className="chip chip-amber">Flag</span> for
                human review.
              </p>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
