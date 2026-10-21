<!--
@component
Collector, spool, and database health (12.2 item 6, UI-001, OBS-003).

The inventory database, the telemetry database, and every collector
position with its state, report age, and spool use. Collector trouble is
shown as collector trouble, in words: it never turns into a device being
down (invariant 2, UI-015).

Example: `<HealthPanel health={overview.health} />`
-->
<script lang="ts">
	import type { HealthView, Reading } from '#lib/api/types.ts';
	import ReadingText from './ReadingText.svelte';
	import StatusBadge from './StatusBadge.svelte';

	interface Props {
		health: HealthView;
	}

	const { health }: Props = $props();

	/**
	 * Map a health reading to a status colour, icon, and label.
	 *
	 * @param r - The reading (`ok`, `healthy`, `degraded`, `overloaded`, `unavailable`, or missing).
	 * @returns Colour, icon, and label for `StatusBadge`.
	 */
	function badge(r: Reading): { color: 'green' | 'amber' | 'red'; icon: string; label: string } {
		if (r.value === 'ok' || r.value === 'healthy')
			return { color: 'green', icon: '✔', label: r.text };
		if (r.value === null) return { color: 'amber', icon: '?', label: r.reason ?? 'unknown' };
		if (r.value === 'unavailable') return { color: 'red', icon: '✖', label: r.text };
		return { color: 'amber', icon: '▲', label: r.text };
	}
</script>

<table class="density-compact">
	<caption>Health of vakr itself (not of the devices)</caption>
	<thead>
		<tr>
			<th scope="col">Component</th>
			<th scope="col">State</th>
			<th scope="col">Report age</th>
			<th scope="col">Spool use</th>
		</tr>
	</thead>
	<tbody>
		{#each [['Inventory database', health.metadata], ['Telemetry database', health.telemetry]] as const as [name, reading] (name)}
			{@const b = badge(reading)}
			<tr>
				<th scope="row">{name}</th>
				<td><StatusBadge small color={b.color} icon={b.icon} label={b.label} /></td>
				<td>—</td>
				<td>—</td>
			</tr>
		{/each}
		{#each health.collectors as c (c.collector_id)}
			{@const b = badge(c.state)}
			<tr>
				<th scope="row">Collector {c.position}</th>
				<td><StatusBadge small color={b.color} icon={b.icon} label={b.label} /></td>
				<td><ReadingText reading={c.age} compact /></td>
				<td><ReadingText reading={c.spool} compact /></td>
			</tr>
		{/each}
	</tbody>
</table>
