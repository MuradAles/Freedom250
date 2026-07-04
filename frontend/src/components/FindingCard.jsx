import React from 'react';
import VerdictBadge from './VerdictBadge';

/** One card per eligibility check (E1-E6): verdict, clickable citation, rationale. */
export default function FindingCard({ finding, onOpenCitation }) {
  const { check_id, verdict, confidence, citation, cited_text, rationale } = finding;

  return (
    <div className="finding-card">
      <div className="finding-card-header">
        <span className="finding-check-id">{check_id}</span>
        <VerdictBadge verdict={verdict} />
        {typeof confidence === 'number' && (
          <span className="finding-confidence muted">{Math.round(confidence * 100)}% confidence</span>
        )}
      </div>
      <p className="finding-rationale">{rationale}</p>
      {citation && (
        <button className="citation-link" onClick={() => onOpenCitation(citation)}>
          {citation}
        </button>
      )}
      {cited_text && <p className="finding-cited-text muted">&ldquo;{cited_text}&rdquo;</p>}
    </div>
  );
}
