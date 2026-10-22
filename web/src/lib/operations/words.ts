/**
 * Operator words for admin operations: where one is, or how it ended (API-011, DISC-T03).
 *
 * The device page, the Admin page, and `vakr inventory rediscover --wait`
 * say the same things in the same order: interfaces found, identity
 * changes, reviews opened, then the duration; a failure says why.
 *
 * @example
 * ```ts
 * outcomeText(op); // 'Succeeded: 24 interfaces, 1 new, 0 renumbered, 2 missing, ...'
 * ```
 *
 * @packageDocumentation
 */
import type { OperationOut, OperationResult } from '#lib/api/types.ts';

/** States of an operation that has not finished. */
export const ACTIVE_STATES: ReadonlySet<string> = new Set(['queued', 'running']);

/**
 * Tell whether an operation is still queued or running.
 *
 * @param op - The operation.
 * @returns True until it ends.
 */
export function isActive(op: Pick<OperationOut, 'state'>): boolean {
	return ACTIVE_STATES.has(op.state);
}

/**
 * Describe what a rediscovery found and changed.
 *
 * @param r - The result counts.
 * @returns One sentence fragment, ending with the duration.
 */
export function resultText(r: OperationResult): string {
	let words =
		`${r.interfaces} interfaces, ${r.new} new, ${r.renumbered} renumbered, ` +
		`${r.missing} missing, ${r.reviews_opened} identity reviews opened`;
	if (r.capability_changes) words += `, ${r.capability_changes} capability changes`;
	if (r.partial) words += ' (partial: some table came back incomplete)';
	return `${words} in ${(r.duration_ms / 1000).toFixed(1)} s`;
}

/**
 * Say where an operation is, or how it ended.
 *
 * @param op - The operation.
 * @returns Such as `Queued: waiting for a collector`, or `Failed: the agent did not answer`.
 */
export function outcomeText(op: Pick<OperationOut, 'state' | 'result' | 'error'>): string {
	if (op.state === 'queued') return 'Queued: waiting for a collector to pick it up';
	if (op.state === 'running') return 'Running: the collector is walking the device';
	if (op.result) return `Succeeded: ${resultText(op.result)}`;
	const state = op.state.charAt(0).toUpperCase() + op.state.slice(1);
	return op.error ? `${state}: ${op.error}` : state;
}
