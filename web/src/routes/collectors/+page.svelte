<!--
@component
Collectors and vakr's own health (12.1 "Collectors/Health", UI-001, OBS-003; WEB-T07).

Each polling position with its latest health report, report age, and spool
use, and the API's own components (databases, sign-in keys) with reasons.
A collector without a recent report is shown as unavailable in words; its
devices are not declared down (invariant 2).
-->
<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { formatInstant } from '#lib/format/time.ts';
	import { collectorsQuery, healthQuery } from '#lib/query/reads-catalog.ts';
	import { devicesHref } from '#lib/links.ts';
	import { pageState } from '#lib/state/page-state.ts';
	import PageStatus from '#lib/components/PageStatus.svelte';
	import ReadingText from '#lib/components/ReadingText.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';

	const collectors = createQuery(() => collectorsQuery());
	const health = createQuery(() => healthQuery());

	/**
	 * Colour, icon, and words for a collector's state.
	 *
	 * @param value - `healthy`, `degraded`, `overloaded`, `unavailable`, or `null` (no report).
	 * @returns Badge properties.
	 */
	function badge(value: unknown): { color: 'green' | 'amber' | 'red'; icon: string } {
		if (value === 'healthy') return { color: 'green', icon: '✔' };
		if (value === 'unavailable') return { color: 'red', icon: '✖' };
		return { color: 'amber', icon: value === null ? '?' : '▲' };
	}
</script>

<svelte:head><title>Collectors and health · vakr</title></svelte:head>

<h1>Collectors and health</h1>
<p class="muted">
	This page is about vakr itself. A collector problem makes device states uncertain; it never marks
	a device down.
</p>

<section aria-labelledby="c-h">
	<h2 id="c-h">Collectors</h2>
	<PageStatus
		state={pageState(collectors, { isEmpty: (d) => d.items.length === 0 })}
		label="Collectors"
		emptyText="No collector has registered yet. Start vakr-collector."
	>
		{#snippet children(d)}
			<div class="table-wrap">
				<table>
					<caption class="sr-only">Collectors</caption>
					<thead>
						<tr>
							<th scope="col">Position</th>
							<th scope="col">State</th>
							<th scope="col">Last report</th>
							<th scope="col">Spool use</th>
							<th scope="col">Enabled</th>
							<th scope="col">Devices</th>
						</tr>
					</thead>
					<tbody>
						{#each d.items as c (c.id)}
							{@const b = badge(c.health?.state.value ?? null)}
							<tr data-collector={c.position}>
								<th scope="row">{c.position} <span class="muted">({c.name})</span></th>
								<td>
									<StatusBadge
										small
										color={b.color}
										icon={b.icon}
										label={c.health
											? c.health.state.value === null
												? (c.health.state.reason ?? 'no report')
												: c.health.state.text
											: 'no health report yet'}
									/>
								</td>
								<td
									>{#if c.health}<ReadingText reading={c.health.age} /> ago{:else}—{/if}</td
								>
								<td
									>{#if c.health}<ReadingText reading={c.health.spool} />{:else}—{/if}</td
								>
								<td>{c.enabled ? 'yes' : 'no'}</td>
								<td><a href={devicesHref({ collector: c.id })}>Devices it has seen</a></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/snippet}
	</PageStatus>
</section>

<section aria-labelledby="api-h">
	<h2 id="api-h">vakr-api</h2>
	<PageStatus state={pageState(health)} label="API health">
		{#snippet children(h)}
			<p>
				State: <strong>{h.status}</strong>{#if h.version}; release {h.version}{/if}{#if h.started_at};
					running since {formatInstant(h.started_at, 'UTC')} UTC{/if}.
			</p>
			<ul>
				{#each h.checks as check (check.name)}
					<li>
						<strong>{check.name}</strong>: {check.status}{check.detail ? ` — ${check.detail}` : ''}
					</li>
				{/each}
			</ul>
		{/snippet}
	</PageStatus>
</section>
