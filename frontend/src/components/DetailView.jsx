import React, { useEffect, useState } from 'react';
import {
  getApplication,
  runEligibilityCheck,
  runAudit,
  isLlmNotConfigured,
} from '../api';
import FindingCard from './FindingCard';
import TransactionsTable from './TransactionsTable';

/** Header (name/program/amount) + tabs (Eligibility | Transactions per `kind`). */
export default function DetailView({ borrowerId, onBack, onOpenCitation }) {
  const [record, setRecord] = useState(null);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState(null);

  function load() {
    setError(null);
    getApplication(borrowerId)
      .then((data) => {
        setRecord(data);
        setTab((prevTab) => prevTab || (data.application ? 'eligibility' : 'transactions'));
      })
      .catch((err) => setError(err.message || 'Failed to load record'));
  }

  useEffect(() => {
    setRecord(null);
    setTab(null);
    setError(null);
    setRunError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [borrowerId]);

  async function handleRun() {
    setRunning(true);
    setRunError(null);
    try {
      if (record.kind === 'application') {
        await runEligibilityCheck(borrowerId);
      } else {
        await runAudit(borrowerId);
      }
      load();
    } catch (err) {
      if (isLlmNotConfigured(err)) {
        setRunError('LLM not configured — set OPENROUTER_API_KEY in backend/.env');
      } else {
        setRunError(err.message || 'Failed to run checks');
      }
    } finally {
      setRunning(false);
    }
  }

  if (error) {
    return (
      <div>
        <button className="link-button" onClick={onBack}>
          &larr; Back to dashboard
        </button>
        <p className="error-text">{error}</p>
      </div>
    );
  }

  if (!record) {
    return (
      <div>
        <button className="link-button" onClick={onBack}>
          &larr; Back to dashboard
        </button>
        <p className="muted">Loading…</p>
      </div>
    );
  }

  const hasApplication = !!record.application;
  const hasTransactions = !!record.transactions;

  return (
    <div>
      <button className="link-button" onClick={onBack}>
        &larr; Back to dashboard
      </button>

      <div className="detail-header">
        <div>
          <h2>{record.business_name}</h2>
          <p className="muted">
            {record.program} &middot; {formatAmount(record.amount)}
          </p>
        </div>
        <button className="btn btn-primary" disabled={running} onClick={handleRun}>
          {running ? 'Running…' : record.kind === 'application' ? 'Run checks' : 'Run audit'}
        </button>
      </div>

      {runError && <p className="error-text">{runError}</p>}

      {hasApplication && hasTransactions && (
        <div className="tabs">
          <button className={`tab ${tab === 'eligibility' ? 'tab-active' : ''}`} onClick={() => setTab('eligibility')}>
            Eligibility
          </button>
          <button className={`tab ${tab === 'transactions' ? 'tab-active' : ''}`} onClick={() => setTab('transactions')}>
            Transactions
          </button>
        </div>
      )}

      {tab === 'eligibility' && hasApplication && (
        <div className="tab-panel">
          {record.eligibility_findings && record.eligibility_findings.length > 0 ? (
            record.eligibility_findings.map((finding) => (
              <FindingCard key={finding.check_id} finding={finding} onOpenCitation={onOpenCitation} />
            ))
          ) : (
            <p className="muted">Not run — click Run checks to evaluate eligibility.</p>
          )}
        </div>
      )}

      {tab === 'transactions' && hasTransactions && (
        <div className="tab-panel">
          <TransactionsTable
            transactions={record.transactions}
            findings={record.audit_findings}
            rollup={record.rollup}
            onOpenCitation={onOpenCitation}
          />
        </div>
      )}
    </div>
  );
}

function formatAmount(amount) {
  if (typeof amount !== 'number') return amount;
  return amount.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}
