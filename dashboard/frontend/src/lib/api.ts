const API = import.meta.env.DEV ? 'http://localhost:8080' : '';

// Sent on all mutating requests for CSRF mitigation (backend checks this header).
// Browsers cannot forge custom headers cross-origin without a CORS preflight,
// which the backend rejects for non-allowlisted origins.
const XHR_HEADER = { 'X-Requested-With': 'XMLHttpRequest' };

/** Extract a human-readable error message from a non-OK response.
 *
 * Tries JSON first (most backend routes return `{error, code}` or
 * `{error, message}`), then falls back to plain text. Always prefixes the
 * status so existing callers that pattern-match on '401'/'403' keep working.
 */
async function buildError(res: Response): Promise<Error> {
  let detail = ''
  try {
    const data = await res.clone().json()
    // Try common error shapes first, then plugin-preview-shaped responses
    // (`{conflicts: [...], manifest, ...}`). Without this, plugin install
    // 409s surfaced as "409 CONFLICT" with no hint at the actual reason
    // (e.g. version mismatch).
    detail =
      data?.error ||
      data?.description ||
      data?.message ||
      (Array.isArray(data?.conflicts) && data.conflicts.length > 0
        ? data.conflicts.join(' • ')
        : '') ||
      (Array.isArray(data?.details) && data.details.length > 0
        ? data.details.join(' • ')
        : '')
  } catch {
    try {
      const text = await res.text()
      // Trim default Flask HTML-error noise; keep payload short.
      detail = text.length < 500 ? text.trim() : ''
    } catch {
      // ignore
    }
  }
  const base = `${res.status} ${res.statusText}`
  return new Error(detail ? `${base}: ${detail}` : base)
}

export const api = {
  get: async (path: string, extraHeaders?: HeadersInit) => {
    const res = await fetch(`${API}/api${path}`, {
      credentials: 'include',
      headers: extraHeaders,
    });
    if (!res.ok) throw await buildError(res);
    return res.json();
  },
  getRaw: async (path: string) => {
    const res = await fetch(`${API}/api${path}`, { credentials: 'include' });
    if (!res.ok) throw await buildError(res);
    return res.text();
  },
  post: async (path: string, body?: unknown) => {
    const res = await fetch(`${API}/api${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...XHR_HEADER },
      credentials: 'include',
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw await buildError(res);
    return res.json();
  },
  put: async (path: string, body?: unknown) => {
    const res = await fetch(`${API}/api${path}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...XHR_HEADER },
      credentials: 'include',
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw await buildError(res);
    return res.json();
  },
  patch: async (path: string, body?: unknown) => {
    const res = await fetch(`${API}/api${path}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...XHR_HEADER },
      credentials: 'include',
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw await buildError(res);
    return res.json();
  },
  delete: async (path: string, body?: unknown) => {
    const res = await fetch(`${API}/api${path}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json', ...XHR_HEADER },
      credentials: 'include',
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw await buildError(res);
    return res.json();
  },
};
