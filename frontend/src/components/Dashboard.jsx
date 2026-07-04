import React, { useEffect, useState } from 'react';
import { getApplications, runEligibilityCheck, runAudit, isLlmNotConfigured } from '../api';
import StatusChip from './StatusChip';
import EvalWidget from './EvalWidget';

/** Table of loan records with a per-row "Run checks"/"Run audit" action. */
export default function Dashboard({ onSelect }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [runningId, setRunningId] = useState(null);
  const [runError, setRunError] = useState(null);

  function load() {
    setError(null);
    getApplications()
      .then(setRows)
      .catch((err) => setError(err.message || 'Failed to load applications'));
  }

  useEffect(() => {
    load();
  }, []);

  async function handleRun(row) {
    setRunningId(row.borrower_id);
    setRunError(null);
    try {
      if (row.kind === 'application') {
        await runEligibilityCheck(row.borrower_id);
      } else {
        await runAudit(row.borrower_id);
      }
      load();
    } catch (err) {
      if (isLlmNotConfigured(err)) {
        setRunError('LLM not configured — set OPENROUTER_API_KEY in backend/.env');
      } else {
        setRunError(err.message || 'Failed to run checks');
      }
    } finally {
      setRunningId(null);
    }
  }

  return (
    <div>
      <EvalWidget />

      <h2 className="section-title">Loan records</h2>
      {error && <p className="error-text">{error}</p>}
      {runError && <p className="error-text">{runError}</p>}

      {rows === null && !error && <p className="muted">Loading applications…</p>}

      {rows && rows.length === 0 && <p className="muted">No loan records found.</p>}

      {rows && rows.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Business</th>
              <th>Program</th>
              <th>Amount</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.borrower_id}>
                <td>
                  <button className="link-button" onClick={() => onSelect(row.borrower_id)}>
                    {row.business_name}
                  </button>
                </td>
                <td>{row.program}</td>
                <td>{formatAmount(row.amount)}</td>
                <td>
                  <StatusChip status={row.status} />
                </td>
                <td>
                  <button
                    className="btn"
                    disabled={runningId === row.borrower_id}
                    onClick={() => handleRun(row)}
                  >
                    {runningId === row.borrower_id
                      ? 'Running…'
                      : row.kind === 'application'
                      ? 'Run checks'
                      : 'Run audit'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function formatAmount(amount) {
  if (typeof amount !== 'number') return amount;
  return amount.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}
