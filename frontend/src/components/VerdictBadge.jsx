import React from 'react';

// Finding-level verdict (pass | flag | fail) and rollup verdict
// (clear | needs_review | possible_fraud) share the same green/amber/red semantics.
const VERDICT_CONFIG = {
  pass: { label: 'Pass', className: 'chip chip-green' },
  clear: { label: 'Clear', className: 'chip chip-green' },
  flag: { label: 'Flag', className: 'chip chip-amber' },
  needs_review: { label: 'Needs review', className: 'chip chip-amber' },
  fail: { label: 'Fail', className: 'chip chip-red' },
  failed: { label: 'Failed', className: 'chip chip-red' },
  possible_fraud: { label: 'Possible fraud', className: 'chip chip-red' },
  not_run: { label: 'Not run', className: 'chip chip-grey' },
};

export default function VerdictBadge({ verdict }) {
  const cfg = VERDICT_CONFIG[verdict] || { label: verdict || 'Unknown', className: 'chip chip-grey' };
  return <span className={cfg.className}>{cfg.label}</span>;
}
