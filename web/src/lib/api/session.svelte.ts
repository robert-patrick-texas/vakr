/**
 * The browser's sign-in session: who is signed in, the CSRF token, and how it ended.
 *
 * The session cookie is `HttpOnly`, so scripts never see it. What the UI
 * knows comes from `GET /api/v1/auth/session` and the sign-in answer: the
 * name, the role (`admin` or `operator`), the CSRF token for writes, and
 * the absolute expiry. When any request answers 401, the session has ended
 * (expired, idle, revoked, or signed out elsewhere): `ended` records why,
 * the layout sends the user to the sign-in page with the current location,
 * and the sign-in page says so instead of showing a bare form (UI-014).
 *
 * A kiosk display signs in differently: `setUpDisplay` sends the kiosk token
 * once, and the API answers with an `HttpOnly` kiosk cookie, so the token is
 * never kept in page script (E.30 #17). `forgetDisplay` drops that cookie.
 *
 * @example
 * ```ts
 * await loadSession();
 * if (session.info?.role === 'admin') { ... }
 * ```
 *
 * @packageDocumentation
 */
import { api, setAuthHooks, unwrap, unwrapEmpty } from './client.ts';
import { ApiFailure } from './errors.ts';
import type { SessionInfo } from './types.ts';

export type SessionStatus = 'unknown' | 'signed-in' | 'signed-out';
export type SessionEnd = 'expired' | 'signed-out' | null;

/** The current session; read it in components, change it only through this module. */
export const session = $state<{
	info: SessionInfo | null;
	status: SessionStatus;
	ended: SessionEnd;
}>({ info: null, status: 'unknown', ended: null });

const listeners: Array<() => void> = [];

/**
 * Run `callback` whenever the session ends (the layout clears cached data and redirects).
 *
 * @param callback - Called after the session state changed to signed out.
 * @returns A function that removes the listener.
 */
export function onSessionEnd(callback: () => void): () => void {
	listeners.push(callback);
	return () => {
		const at = listeners.indexOf(callback);
		if (at >= 0) listeners.splice(at, 1);
	};
}

/**
 * Record that the session is gone.
 *
 * @param why - `expired` after a 401, `signed-out` after sign-out.
 */
function end(why: Exclude<SessionEnd, null>): void {
	const wasSignedIn = session.status === 'signed-in';
	session.info = null;
	session.status = 'signed-out';
	session.ended = wasSignedIn || why === 'signed-out' ? why : session.ended;
	listeners.forEach((callback) => callback());
}

setAuthHooks({
	csrf: () => session.info?.csrf_token ?? null,
	lost: () => {
		if (session.status === 'signed-in') end('expired');
	}
});

/**
 * Ask the API who the browser is signed in as.
 *
 * @returns The session status after the answer.
 * @throws ApiFailure when the API is unavailable (not for "signed out").
 */
export async function loadSession(): Promise<SessionStatus> {
	try {
		const info = await unwrap(api.GET('/api/v1/auth/session'));
		session.info = info;
		session.status = 'signed-in';
	} catch (error) {
		if (!(error instanceof ApiFailure) || error.kind !== 'unauthenticated') throw error;
		session.info = null;
		session.status = 'signed-out';
	}
	return session.status;
}

/**
 * Sign in with a username and password; the API sets the cookie.
 *
 * @param username - The account.
 * @param password - Its password; sent once in the JSON body, never stored.
 * @returns The new session.
 * @throws ApiFailure for a refused sign-in (401, 429 with `retryAfter`, 403 over plain HTTP).
 */
export async function signIn(username: string, password: string): Promise<SessionInfo> {
	const info = await unwrap(api.POST('/api/v1/auth/login', { body: { username, password } }));
	session.info = info;
	session.status = 'signed-in';
	session.ended = null;
	return info;
}

/**
 * Sign out: the API ends the session and clears the cookie.
 *
 * @throws ApiFailure when the API refuses or is unavailable; the local state is cleared anyway.
 */
export async function signOut(): Promise<void> {
	try {
		await unwrapEmpty(api.POST('/api/v1/auth/logout'));
	} finally {
		end('signed-out');
	}
}

/**
 * Set this browser up as a kiosk display with a kiosk token; the API sets the kiosk cookie.
 *
 * @param token - The `vkk_...` token an admin handed over; sent once, never stored.
 * @returns The display's identity and the token's expiry.
 * @throws ApiFailure for a refused token (401), plain HTTP (403), or no answer.
 */
export async function setUpDisplay(token: string): Promise<SessionInfo> {
	const info = await unwrap(api.POST('/api/v1/auth/kiosk', { body: { token } }));
	session.info = info;
	session.status = 'signed-in';
	session.ended = null;
	return info;
}

/**
 * Make this browser forget its kiosk cookie; the token stays valid elsewhere.
 *
 * @throws ApiFailure when the API refuses or is unavailable; the local state is cleared anyway.
 */
export async function forgetDisplay(): Promise<void> {
	try {
		await unwrapEmpty(api.POST('/api/v1/auth/kiosk/forget'));
	} finally {
		if (session.info?.kind === 'kiosk') end('signed-out');
	}
}

/**
 * Tell whether the signed-in user may make admin changes (the API checks again).
 *
 * @returns True for the admin role.
 */
export function isAdmin(): boolean {
	return session.info?.role === 'admin';
}
