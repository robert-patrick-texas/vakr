"""Unit tests: pure logic and in-process behavior, no network or databases.

Unit tests never open sockets or start databases, so they run in seconds on a
laptop or CI runner and a failure points at vakr code, not at the environment.

Dependencies:
    None.

Example:
    $ uv run pytest tests/unit
"""
