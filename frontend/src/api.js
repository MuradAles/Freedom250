// Fetch helpers for the SBA Loan Compliance Checker API.
// Base path is `/api`; in dev this is proxied to the backend via package.json's
// "proxy" field, in prod it's served from the same origin.

const API_BASE = '/api';

/** Thrown for any non-OK response. Carries the HTTP status and a best-effort
 * `detail` message so the UI can special-case things like 503 "LLM not configured". */
export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `Request failed with status ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, options) {
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, options);
  } catch (err) {
    throw new ApiError(0, 'Could not reach the server. Is the backend running?');
  }

  if (!res.ok) {
    let detail = `Request failed with status ${res.status}`;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail;
    } catch {
      // response body wasn't JSON; keep the generic message
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return null;
  return res.json();
}

/** Is this error the "LLM not configured" case (missing API key)? */
export function isLlmNotConfigured(err) {
  return err instanceof ApiError && err.status === 503;
}

export function getApplications() {
  return request('/applications');
}

export function getApplication(borrowerId) {
  return request(`/applications/${encodeURIComponent(borrowerId)}`);
}

export function runEligibilityCheck(borrowerId) {
  return request(`/applications/${encodeURIComponent(borrowerId)}/check`, {
    method: 'POST',
  });
}

export function runAudit(borrowerId) {
  return request(`/applications/${encodeURIComponent(borrowerId)}/audit`, {
    method: 'POST',
  });
}

export function getRegulation(citation) {
  return request(`/regulations/${encodeURIComponent(citation)}`);
}

export function getEval(limit) {
  const qs = limit ? `?limit=${encodeURIComponent(limit)}` : '';
  return request(`/eval${qs}`);
}
