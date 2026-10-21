/**
 * The typed API v1 client: every request of the web UI goes through it (ADR-035 decision 5).
 *
 * `api` is an `openapi-fetch` client over the generated `paths` type, so
 * every path, parameter, and answer is checked at build time. It talks to
 * the same origin only (`connect-src 'self'`), sends the session cookie,
 * and adds `X-CSRF-Token` to every state-changing request (SEC-011). A 401
 * on any request other than sign-in tells the session module the session
 * has ended, so the page can say so and offer sign-in (UI-014).
 *
 * `unwrap` turns an `openapi-fetch` result into the answer or an
 * `ApiFailure`; TanStack Query then decides retries from its `kind`.
 *
 * @example
 * ```ts
 * const page = await unwrap(api.GET('/api/v1/devices', { params: { query: { limit: 50 } } }));
 * ```
 *
 * @packageDocumentation
 */
import createClient, { type Middleware } from 'openapi-fetch';
import { ApiFailure, failureFromAnswer, networkFailure } from './errors.ts';
import type { paths } from './schema.d.ts';

type AuthHooks = { csrf: () => string | null; lost: () => void };
const hooks: AuthHooks = { csrf: () => null, lost: () => undefined };
const SAFE = new Set(['GET', 'HEAD', 'OPTIONS']);
const NO_LOSS = ['/api/v1/auth/login', '/api/v1/auth/session'];

/**
 * Connect the client to the session: where the CSRF token comes from, what a 401 does.
 *
 * @param next - The session module's hooks.
 */
export function setAuthHooks(next: AuthHooks): void {
	hooks.csrf = next.csrf;
	hooks.lost = next.lost;
}

const auth: Middleware = {
	onRequest({ request }) {
		const token = hooks.csrf();
		if (!SAFE.has(request.method) && token) request.headers.set('X-CSRF-Token', token);
		return request;
	},
	onResponse({ request, response }) {
		const path = new URL(request.url).pathname;
		if (response.status === 401 && !NO_LOSS.includes(path)) hooks.lost();
		return response;
	}
};

export const api = createClient<paths>({ baseUrl: '', credentials: 'same-origin' });
api.use(auth);

type Result<T> = { data?: T; error?: unknown; response: Response };

/**
 * Wait for a request and return its answer, or throw what went wrong.
 *
 * @param pending - An `api.GET(...)` or `api.POST(...)` call.
 * @returns The parsed answer.
 * @throws ApiFailure for a non-2xx answer or no answer at all.
 */
export async function unwrap<T>(pending: Promise<Result<T>>): Promise<T> {
	let result: Result<T>;
	try {
		result = await pending;
	} catch (error) {
		if (error instanceof ApiFailure) throw error;
		throw networkFailure();
	}
	if (result.response.ok && result.data !== undefined) return result.data;
	const retry = result.response.headers.get('retry-after');
	throw failureFromAnswer(result.response.status, result.error, retry);
}

/**
 * Wait for a request whose success has no body (204), or throw what went wrong.
 *
 * @param pending - An `api.POST(...)` call.
 * @throws ApiFailure for a non-2xx answer or no answer at all.
 */
export async function unwrapEmpty(pending: Promise<Result<unknown>>): Promise<void> {
	let result: Result<unknown>;
	try {
		result = await pending;
	} catch {
		throw networkFailure();
	}
	if (!result.response.ok) {
		throw failureFromAnswer(result.response.status, result.error, null);
	}
}
