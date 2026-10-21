"""End-to-end harness: a real ``vakr api serve`` on seeded SQLite databases for Playwright.

The web UI's Playwright suite (``web/e2e``, WEB-T01 to WEB-T11) runs against
the API process exactly as the package runs it. This package builds a
relocated install, seeds metadata and telemetry with times relative to the
real clock, creates the test accounts, starts the server, and keeps the
fleet fresh while the browser tests run (``serve``).

Dependencies:
    vakr: The stores, auth services, and the ``vakr`` command.
    tests.contract.contract_samples: Valid domain records.

Example:
    $ uv run python -m tests.e2e.serve --port 18990 --web-dir web/build --fixtures-out fx.json
"""
