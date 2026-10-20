"""Integration tests: real sockets, files, or processes on this host, no external network.

Each test skips with a clear reason when the host cannot provide what it
needs, so the suite stays green on restricted CI runners.

Dependencies:
    pytest.

Example:
    $ uv run pytest tests/integration
"""
