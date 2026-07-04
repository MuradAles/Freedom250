import React, { useEffect, useState } from 'react';
import { getRegulation, ApiError } from '../api';

/**
 * Slide-over panel showing the verbatim Title 13 text behind a citation.
 * Controlled by the parent: `citation` is the string to look up, `onClose`
 * closes the drawer. Renders nothing (well, a hidden aside) when citation is null.
 */
export default function CitationDrawer({ citation, onClose }) {
  const [state, setState] = useState({ loading: false, error: null, data: null });

  useEffect(() => {
    if (!citation) return;
    let cancelled = false;
    setState({ loading: true, error: null, data: null });
    getRegulation(citation)
      .then((data) => {
        if (!cancelled) setState({ loading: false, error: null, data });
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setState({ loading: false, error: 'not_found', data: null });
        } else {
          setState({ loading: false, error: err.message || 'Failed to load citation', data: null });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [citation]);

  const open = !!citation;

  return (
    <>
      <div
        className={`drawer-overlay ${open ? 'drawer-overlay-open' : ''}`}
        onClick={onClose}
        aria-hidden={!open}
      />
      <aside className={`citation-drawer ${open ? 'citation-drawer-open' : ''}`} aria-hidden={!open}>
        <div className="drawer-header">
          <h2>{citation || ''}</h2>
          <button className="drawer-close" onClick={onClose} aria-label="Close">
            &times;
          </button>
        </div>
        <div className="drawer-body">
          {state.loading && <p className="muted">Loading regulation text…</p>}
          {state.error === 'not_found' && (
            <p className="error-text">Citation not found in the indexed Title 13 text.</p>
          )}
          {state.error && state.error !== 'not_found' && (
            <p className="error-text">{state.error}</p>
          )}
          {state.data && (
            <>
              <p className="drawer-section-label">
                Part {state.data.part} &middot; §{state.data.section}
              </p>
              <h3 className="drawer-heading">{state.data.heading}</h3>
              <pre className="drawer-text">{state.data.text}</pre>
            </>
          )}
        </div>
      </aside>
    </>
  );
}
