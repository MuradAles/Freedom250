import React, { useEffect, useState } from 'react';
import { getEval, isLlmNotConfigured } from '../api';

const LEVEL_LABELS = {
  transaction_level: 'Transactions',
  business_level: 'Businesses',
  submission_level: 'Submissions',
};

/** Compact "LLM vs ground truth: N/60 agree (X%)" widget, all three levels if space allows. */
export default function EvalWidget() {
  const [state, setState] = useState({ loading: true, error: null, notConfigured: false, data: null });

  useEffect(() => {
    getEval(60)
      .then((data) => setState({ loading: false, error: null, notConfigured: false, data }))
      .catch((err) => {
        if (isLlmNotConfigured(err)) {
          setState({ loading: false, error: null, notConfigured: true, data: null });
        } else {
          setState({ loading: false, error: err.message || 'Failed to load eval stats', notConfigured: false, data: null });
        }
      });
  }, []);

  if (state.loading) {
    return (
      <div className="eval-widget">
        <p className="muted">Loading eval stats…</p>
      </div>
    );
  }

  if (state.notConfigured) {
    return (
      <div className="eval-widget">
        <p className="muted">LLM not configured — set OPENROUTER_API_KEY in backend/.env to see eval stats.</p>
      </div>
    );
  }

  if (state.error) {
    return (
      <div className="eval-widget">
        <p className="error-text">{state.error}</p>
      </div>
    );
  }

  const levels = Object.keys(LEVEL_LABELS).filter((key) => state.data && state.data[key]);

  if (levels.length === 0) {
    return (
      <div className="eval-widget">
        <p className="muted">No eval data available.</p>
      </div>
    );
  }

  return (
    <div className="eval-widget">
      <h3 className="eval-widget-title">LLM vs ground truth</h3>
      <div className="eval-widget-stats">
        {levels.map((key) => {
          const level = state.data[key];
          return (
            <div className="eval-stat" key={key}>
              <span className="eval-stat-label">{LEVEL_LABELS[key]}</span>
              <span className="eval-stat-value">
                {level.agree}/{level.total} agree ({level.agreement_pct.toFixed(1)}%)
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
