import React from 'react';

// Dashboard-level status: not_run | clear | flagged | failed
const STATUS_CONFIG = {
  not_run: { label: 'Not run', className: 'chip chip-grey' },
  clear: { label: 'Clear', className: 'chip chip-green' },
  flagged: { label: 'Flagged', className: 'chip chip-amber' },
  failed: { label: 'Failed', className: 'chip chip-red' },
};

export default function StatusChip({ status }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.not_run;
  return <span className={cfg.className}>{cfg.label}</span>;
}
