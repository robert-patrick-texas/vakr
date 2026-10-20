"""Fixtures for the ADR-021 benchmark unit tests.

Dependencies:
    pytest: Fixture machinery.
    sqlc_testkit: The list of connections a test opened.

Example:
    $ uv run pytest tests/performance/sqlite_compact
"""

from collections.abc import Iterator

import pytest

from tests.performance.sqlite_compact.sqlc_testkit import OPEN


@pytest.fixture(autouse=True)
def _close_connections() -> Iterator[None]:
    """Close every connection the test opened through the test kit.

    Args:
        None

    Returns:
        Iterator[None]: Yields once while the test runs.

    Raises:
        None
    """
    yield
    while OPEN:
        OPEN.pop().close()
