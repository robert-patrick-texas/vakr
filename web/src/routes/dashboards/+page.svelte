<!--
@component
The dashboards available in this release (12.1 "Dashboards", UI-008).

The built-in dashboards: the overview, rankings, high-priority interfaces,
and WAN circuits, each also as a kiosk display (UI-013). Building and
pinning one's own dashboards (UI-004) and
public dashboards (UI-007) come in a later release; the page says so.
-->
<svelte:head><title>Dashboards · vakr</title></svelte:head>

<h1>Dashboards</h1>
<div class="grid">
	<a class="card tile" href="/">
		<h2>Overview</h2>
		<p>Device state, health, high priority, WAN, top utilization and errors, events.</p>
	</a>
	<a class="card tile" href="/rankings">
		<h2>Rankings</h2>
		<p>Top utilization (p95) and top errors for 15 minutes to 24 hours, with filters.</p>
	</a>
	<a class="card tile" href="/high-priority">
		<h2>High-priority interfaces</h2>
		<p>Every primary-interest interface with current values.</p>
	</a>
	<a class="card tile" href="/wan">
		<h2>WAN circuits</h2>
		<p>Circuits with provider, circuit ID, contracted rate, and current use.</p>
	</a>
	<a class="card tile" href="/kiosk">
		<h2>Kiosk displays</h2>
		<p>These dashboards for a wall display: large type, no navigation, optional rotation.</p>
	</a>
</div>
<p class="muted">
	Your own dashboards (pinning interfaces across devices) and public dashboards are not in this
	release.
</p>

<style>
	.tile {
		text-decoration: none;
		color: var(--fg);
	}
	.tile h2 {
		margin-top: 0;
		color: var(--link);
	}
</style>
