"""vakr's own metrics in Prometheus text format (OBS-001, OBS-004, OBS-005, OBS-007, E.33).

``GET /api/v1/metrics`` on ``vakr-api`` answers the metrics of the API
process itself, of the databases and inventory, and of the collector. The
collector has no listener of its own: it writes a snapshot file every 15 s
(``vakr.metrics.snapshot``) that the API reads at each scrape. This package
holds the pieces both processes share, framework-free:

- ``exposition``: the text format 0.0.4 writer, with series and size caps;
- ``catalog``: every metric family, its type, help, and fixed label values;
- ``process``: CPU, memory, and file descriptors of the current process;
- ``snapshot``: the collector's snapshot file, written and read strictly;
- ``requests``: API request counts and durations by fixed route group;
- ``service``: what one scrape of ``vakr-api`` collects and caches.

Dependencies:
    Standard library and ``vakr.logic`` (``window`` holds the rolling
    summaries); no web framework and no database driver. Callers inject
    the database readers.

Safety:
    No metric carries a device, interface, user, address, path, or token:
    every label value comes from a fixed set (E.33).

Example:
    >>> import vakr.metrics.exposition as exposition
    >>> exposition.CONTENT_TYPE
    'text/plain; version=0.0.4; charset=utf-8'
"""
