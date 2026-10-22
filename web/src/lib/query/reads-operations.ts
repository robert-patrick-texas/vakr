/**
 * Query options for admin operations: one operation, and the newest ones (API-011, E.31, E.32).
 *
 * An operation is polled every 2 s while it is queued or running and not
 * at all once it ended; a list polls every 2 s while any row is active and
 * on the list interval otherwise, so following a rediscovery costs one
 * small read per 2 s and nothing after it finished.
 *
 * @example
 * ```ts
 * createQuery(() => operationQuery(id));
 * ```
 *
 * @packageDocumentation
 */
import { api, unwrap } from '#lib/api/client.ts';
import type { OperationOut, OperationPage } from '#lib/api/types.ts';
import { isActive } from '#lib/operations/words.ts';
import { REFRESH } from './policy.ts';

/**
 * One operation, polled while it is active.
 *
 * @param id - Operation UUID.
 * @returns Query options.
 */
export function operationQuery(id: string) {
	return {
		queryKey: ['operation', id] as const,
		queryFn: () =>
			unwrap(
				api.GET('/api/v1/operations/{operation_uuid}', {
					params: { path: { operation_uuid: id } }
				})
			),
		refetchInterval: (q: { state: { data?: OperationOut } }) =>
			q.state.data && !isActive(q.state.data) ? false : REFRESH.operation
	};
}

/**
 * The newest operations, of one device or of all.
 *
 * @param device - Device UUID, or `null` for every device.
 * @param limit - Rows, 1 to 500.
 * @returns Query options.
 */
export function operationsQuery(device: string | null, limit: number) {
	return {
		queryKey: ['operations', device, limit] as const,
		queryFn: () => unwrap(api.GET('/api/v1/operations', { params: { query: { device, limit } } })),
		refetchInterval: (q: { state: { data?: OperationPage } }) =>
			q.state.data?.rows.some(isActive) ? REFRESH.operation : REFRESH.list
	};
}
