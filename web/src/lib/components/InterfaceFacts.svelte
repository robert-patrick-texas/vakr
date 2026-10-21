<!--
@component
An interface's identity, circuit metadata, addressing, and current values (UI-002, 12.5).

Identity is the persistent UUID; `ifIndex` is shown as the currently
observed index only (it is never identity). Speeds, the capacity override,
and the contracted rate are shown with their units; a missing value is a
dash with no zero implied.

Example: `<InterfaceFacts detail={answer} />`
-->
<script lang="ts">
	import type { InterfaceDetail } from '#lib/api/types.ts';
	import { bitRate } from '#lib/format/units.ts';
	import ReadingText from './ReadingText.svelte';

	interface Props {
		detail: InterfaceDetail;
	}

	const { detail }: Props = $props();
	const i = $derived(detail.interface);
	const c = $derived(detail.current?.current ?? null);
	const dash = (v: string | number | null | undefined) =>
		v === null || v === undefined || v === '' ? '—' : String(v);
</script>

<div class="grid facts-grid">
	<section class="card" aria-labelledby="ident-h">
		<h2 id="ident-h">Identity and circuit</h2>
		<dl class="facts">
			<dt>UUID</dt>
			<dd class="mono wrap">{i.id}</dd>
			<dt>ifName / ifDescr</dt>
			<dd>{dash(i.if_name)} / {dash(i.if_descr)}</dd>
			<dt>ifAlias</dt>
			<dd>{dash(i.if_alias)}</dd>
			<dt>ifIndex (now)</dt>
			<dd>{dash(i.if_index)}</dd>
			<dt>ifType</dt>
			<dd>{dash(i.if_type)}</dd>
			<dt>Lifecycle</dt>
			<dd>{i.lifecycle}</dd>
			<dt>Interest</dt>
			<dd>{i.interest === 'primary' ? 'High priority' : 'Other'}</dd>
			<dt>Sticky / WAN circuit</dt>
			<dd>{i.sticky ? 'sticky' : 'not sticky'} / {i.is_wan_circuit ? 'WAN circuit' : 'not WAN'}</dd>
			<dt>Provider / circuit</dt>
			<dd>{dash(i.provider)} / {dash(i.circuit_id)}</dd>
			<dt>Contracted rate</dt>
			<dd>{bitRate(i.contracted_bps)}</dd>
			<dt>ifSpeed / ifHighSpeed</dt>
			<dd>
				{bitRate(i.if_speed)} / {i.if_high_speed === null ? '—' : bitRate(i.if_high_speed * 1e6)}
			</dd>
			<dt>Capacity override</dt>
			<dd>{bitRate(i.capacity_override_bps)}</dd>
			<dt>Addresses</dt>
			<dd class="mono">{i.addresses.join(', ') || '—'}</dd>
			<dt>MAC</dt>
			<dd class="mono">{dash(i.mac)}</dd>
			<dt>Managed by</dt>
			<dd>{i.managed_by}</dd>
			<dt>Comment</dt>
			<dd>{dash(i.comment)}</dd>
		</dl>
	</section>
	<section class="card" aria-labelledby="now-h">
		<h2 id="now-h">Current values ({detail.position})</h2>
		{#if c === null}
			<p class="muted">
				No current values: <ReadingText reading={detail.telemetry} />
			</p>
		{:else}
			<dl class="facts" data-testid="current-values">
				<dt>Admin / oper</dt>
				<dd><ReadingText reading={c.state} /></dd>
				<dt>In / out</dt>
				<dd><ReadingText reading={c.in_rate} /> / <ReadingText reading={c.out_rate} /></dd>
				<dt>Utilization in / out</dt>
				<dd><ReadingText reading={c.in_util} /> / <ReadingText reading={c.out_util} /></dd>
				<dt>Packets in / out</dt>
				<dd><ReadingText reading={c.in_pps} /> / <ReadingText reading={c.out_pps} /></dd>
				<dt>Errors</dt>
				<dd><ReadingText reading={c.errors} /></dd>
				<dt>Capacity</dt>
				<dd><ReadingText reading={c.capacity} /></dd>
				<dt>Age</dt>
				<dd><ReadingText reading={c.age} /></dd>
			</dl>
		{/if}
	</section>
</div>

<style>
	.facts-grid {
		grid-template-columns: repeat(auto-fill, minmax(min(30rem, 100%), 1fr));
	}
	.wrap {
		overflow-wrap: anywhere;
	}
</style>
