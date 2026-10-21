<!--
@component
The page for an address the web UI does not have (the client router's 404).

The server answers every non-API path with the app (the single-page-app
fallback), so an unknown address is reported here, in the app's frame.
-->
<script lang="ts">
	import { page } from '$app/state';
</script>

<svelte:head><title>Not found · vakr</title></svelte:head>

<h1>{page.status === 404 ? 'Page not found' : 'Something went wrong'}</h1>
<p class="notice notice-gray" role="alert" data-state="not-found">
	{page.status === 404
		? `There is no page at ${page.url.pathname}.`
		: (page.error?.message ?? 'The page failed.')}
	<a href="/">Go to the overview</a>.
</p>
