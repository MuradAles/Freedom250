import React from 'react';
import VerdictBadge from './VerdictBadge';

/**
 * Transaction rows with verdict badges (looked up by `subject: "transaction:<row>"`),
 * flagged/failed rows highlighted, and a rollup verdict banner at the top.
 */
export default function TransactionsTable({ transactions, findings, rollup, onOpenCitation }) {
  const findingByRow = new Map();
  (findings || []).forEach((f) => {
    if (typeof f.subject === 'string' && f.subject.startsWith('transaction:')) {
      const row = Number(f.subject.split(':')[1]);
      findingByRow.set(row, f);
    }
  });

  return (
    <div>
      {rollup && (
        <div className={`rollup-banner rollup-${rollup.verdict}`}>
          <VerdictBadge verdict={rollup.verdict} />
          <span>{rollup.rationale}</span>
        </div>
      )}
      {!rollup && <p className="muted">Not run — click Run audit to check transactions.</p>}

      {transactions && transactions.length > 0 ? (
        <table className="data-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Amount</th>
              <th>Category</th>
              <th>Merchant</th>
              <th>Description</th>
              <th>Owner related</th>
              <th>Verdict</th>
              <th>Citation</th>
            </tr>
          </thead>
          <tbody>
            {transactions.map((tx) => {
              const finding = findingByRow.get(tx.row);
              const rowClass =
                finding && (finding.verdict === 'flag' || finding.verdict === 'fail')
                  ? `row-highlight-${finding.verdict}`
                  : '';
              return (
                <tr key={tx.row} className={rowClass}>
                  <td>{tx.date}</td>
                  <td>{formatAmount(tx.amount)}</td>
                  <td>{tx.category}</td>
                  <td>{tx.merchant}</td>
                  <td>{tx.description}</td>
                  <td>{tx.owner_related ? 'Yes' : 'No'}</td>
                  <td>{finding ? <VerdictBadge verdict={finding.verdict} /> : <VerdictBadge verdict="not_run" />}</td>
                  <td>
                    {finding && finding.citation ? (
                      <button className="citation-link" onClick={() => onOpenCitation(finding.citation)}>
                        {finding.citation}
                      </button>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <p className="muted">No transactions available for this business.</p>
      )}
    </div>
  );
}

function formatAmount(amount) {
  if (typeof amount !== 'number') return amount;
  return amount.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}
