"""Unit tests for ``vakr.query``: the current-state services behind ``vakr status`` and the TUI.

The services are exercised through the memory adapters, populated with
domain objects through the ports exactly as the collector would write
them, so every test also checks that the services use nothing but ports.

Dependencies:
    pytest; vakr.adapters.memory; tests.unit.query.query_testkit.

Example:
    $ uv run pytest tests/unit/query
"""
