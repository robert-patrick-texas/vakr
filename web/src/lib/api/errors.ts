/**
 * API failures as the web UI understands them: what went wrong, in operator words (UI-014).
 *
 * Every non-2xx answer of API v1 carries the error envelope (`code`,
 * `message`, `request_id`, `fields`). `ApiFailure` keeps those and adds a
 * `kind` that decides the page state: signed out, permission denied, not
 * found, a refused parameter, or the backend being unavailable. A network
 * error (vakr-api not answering) is `unavailable` too: storage or service
 * trouble is never shown as a device being down (invariant 2).
 *
 * @example
 * ```ts
 * const f = failureFromAnswer(503, { code: 'storage_unavailable', message: 'x', request_id: 'r', fields: [] });
 * f.kind; // 'unavailable'
 * ```
 *
 * @packageDocumentation
 */
import type { ErrorEnvelope } from './types.ts';

export type FailureKind =
	| 'unauthenticated'
	| 'forbidden'
	| 'not_found'
	| 'invalid'
	| 'conflict'
	| 'throttled'
	| 'unavailable'
	| 'server';

/** One failed API request. */
export class ApiFailure extends Error {
	readonly kind: FailureKind;
	readonly status: number;
	readonly code: string;
	readonly requestId: string | null;
	readonly fields: ErrorEnvelope['fields'];
	readonly retryAfter: number | null;

	/**
	 * Build a failure.
	 *
	 * @param kind - What the page should show.
	 * @param status - HTTP status, or 0 when nothing answered.
	 * @param envelope - The error envelope, when the API sent one.
	 * @param retryAfter - Seconds from `Retry-After`, if any.
	 */
	constructor(
		kind: FailureKind,
		status: number,
		envelope: Partial<ErrorEnvelope>,
		retryAfter: number | null = null
	) {
		super(envelope.message ?? `request failed (${status})`);
		this.name = 'ApiFailure';
		this.kind = kind;
		this.status = status;
		this.code = envelope.code ?? 'unknown';
		this.requestId = envelope.request_id ?? null;
		this.fields = envelope.fields ?? [];
		this.retryAfter = retryAfter;
	}
}

/**
 * Map an HTTP status to the page-level kind of failure.
 *
 * @param status - HTTP status.
 * @returns The kind.
 */
export function kindOf(status: number): FailureKind {
	if (status === 401) return 'unauthenticated';
	if (status === 403) return 'forbidden';
	if (status === 404) return 'not_found';
	if (status === 409) return 'conflict';
	if (status === 429) return 'throttled';
	if (status === 400 || status === 413 || status === 415 || status === 422) return 'invalid';
	if (status === 502 || status === 503 || status === 504) return 'unavailable';
	return 'server';
}

/**
 * Build the failure for an answer that was not 2xx.
 *
 * @param status - HTTP status.
 * @param body - The parsed body; the envelope when the API sent one.
 * @param retryAfter - The `Retry-After` header, if any.
 * @returns The failure.
 */
export function failureFromAnswer(
	status: number,
	body: unknown,
	retryAfter: string | null = null
): ApiFailure {
	const envelope =
		typeof body === 'object' && body !== null ? (body as Partial<ErrorEnvelope>) : {};
	const seconds = retryAfter !== null && /^\d+$/.test(retryAfter) ? Number(retryAfter) : null;
	return new ApiFailure(kindOf(status), status, envelope, seconds);
}

/**
 * Build the failure for a request that got no answer at all.
 *
 * @returns An `unavailable` failure with status 0.
 */
export function networkFailure(): ApiFailure {
	return new ApiFailure('unavailable', 0, {
		code: 'no_answer',
		message: 'vakr-api did not answer; the service or the network to it is down'
	});
}

/**
 * Tell whether an idempotent read may be retried after this error (ADR-035 decision 6).
 *
 * @param error - What the request threw.
 * @returns True for no answer and 502/503/504; false for every 4xx and 500.
 */
export function isRetryable(error: unknown): boolean {
	return error instanceof ApiFailure ? error.kind === 'unavailable' : false;
}

/**
 * Describe a failure for a page, without implying more than is known (UI-015).
 *
 * @param error - What the request threw.
 * @returns A heading and a sentence.
 */
export function describeFailure(error: unknown): { title: string; detail: string } {
	if (!(error instanceof ApiFailure)) {
		return { title: 'Something went wrong', detail: String(error) };
	}
	const ref = error.requestId ? ` (request ${error.requestId})` : '';
	switch (error.kind) {
		case 'unauthenticated':
			return { title: 'Signed out', detail: 'Your session has ended. Sign in again.' };
		case 'forbidden':
			return { title: 'Permission denied', detail: `Your role cannot do this${ref}.` };
		case 'not_found':
			return { title: 'Not found', detail: `${error.message}${ref}.` };
		case 'unavailable':
			return {
				title: 'Backend unavailable',
				detail: `${error.message}${ref}. This says nothing about the devices themselves.`
			};
		default:
			return { title: 'Request refused', detail: `${error.message}${ref}.` };
	}
}
