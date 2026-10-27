/**
 * Query options for the records manual inventory edits start from (27.4, META-008).
 *
 * A device's editable record and an interface's annotation carry their
 * owner and `version`, so a form can say which file manages a record and
 * send the version its change is based on (DB-003). A form keeps its own
 * draft, so these answers are not polled: a refresh would not change what
 * the admin is typing, and a stale version is caught by the API instead.
 *
 * @example
 * ```ts
 * createQuery(() => deviceRecordQuery(id));
 * ```
 *
 * @packageDocumentation
 */
import { api, unwrap } from '#lib/api/client.ts';

/**
 * A device's editable record.
 *
 * @param id - Device UUID.
 * @returns Query options.
 */
export function deviceRecordQuery(id: string) {
	return {
		queryKey: ['device-record', id] as const,
		queryFn: () =>
			unwrap(
				api.GET('/api/v1/devices/{device_uuid}/record', {
					params: { path: { device_uuid: id } }
				})
			),
		refetchInterval: false as const
	};
}

/**
 * An interface's annotation, vakr tags, and description tags.
 *
 * @param id - Interface UUID.
 * @returns Query options.
 */
export function annotationQuery(id: string) {
	return {
		queryKey: ['annotation', id] as const,
		queryFn: () =>
			unwrap(
				api.GET('/api/v1/interfaces/{interface_uuid}/annotation', {
					params: { path: { interface_uuid: id } }
				})
			),
		refetchInterval: false as const
	};
}
