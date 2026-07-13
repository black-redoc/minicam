/**
 * authedFetch — shared fetch() wrapper for every authenticated page.
 *
 * A 401 always means "no valid session" (see get_current_user in
 * app/dependencies.py: missing cookie, expired/invalid JWT, revoked
 * session, or deactivated user) — never "forbidden" (403, e.g. non-admin
 * or the WAN access gate) or "not found" (404, e.g. no permission on that
 * specific camera). Only 401 means the whole page should go back to
 * /login; 403/404 are left to each caller to handle, since the user IS
 * still logged in.
 */
/* exported authedFetch, SessionExpiredError, logout */

class SessionExpiredError extends Error {}

async function authedFetch(url, options) {
    const res = await fetch(url, options);
    if (res.status === 401) {
        window.location.href = '/login';
        throw new SessionExpiredError(`${url} returned 401 — session expired`);
    }
    return res;
}

/**
 * Revoke the current session on the server, then always return to /login —
 * even if the request itself fails, the user shouldn't be stranded on an
 * authenticated page.
 */
async function logout() {
    try {
        await authedFetch('/api/auth/logout', { method: 'POST' });
    } catch {
        // authedFetch already redirects on a 401; any other failure
        // shouldn't strand the user on this page either.
    } finally {
        window.location.href = '/login';
    }
}
