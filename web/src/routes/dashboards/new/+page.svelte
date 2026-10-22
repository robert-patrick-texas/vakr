<!--
@component
A new custom dashboard (UI-004; admins only).

Operators are told who can create dashboards; the API refuses them anyway.
-->
<script lang="ts">
	import { isAdmin } from '#lib/api/session.svelte.ts';
	import DashboardEditor from '#lib/dashboards/DashboardEditor.svelte';
	import { newDraft } from '#lib/dashboards/editor.ts';
</script>

<svelte:head><title>New dashboard · vakr</title></svelte:head>

<p><a href="/dashboards">← Dashboards</a></p>
<h1>New dashboard</h1>
{#if isAdmin()}
	<DashboardEditor initial={newDraft()} id={null} version={null} names={{}} />
{:else}
	<p class="notice notice-amber">Only an admin can create dashboards.</p>
{/if}
