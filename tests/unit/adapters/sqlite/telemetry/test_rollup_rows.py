"""Decoding the rollup job's slim interface rows (RET-004, invariant 1).

Dependencies:
    pytest; vakr.adapters.sqlite.telemetry.rollup_rows.

Example:
    $ uv run pytest tests/unit/adapters/sqlite/telemetry/test_rollup_rows.py
"""

import pytest

from vakr.adapters.sqlite.telemetry.rollup_rows import row_key, stored_real
from vakr.domain.errors import StorageError


def test_stored_rates_keep_null_as_none_and_refuse_foreign_values() -> None:
    """Invariant 1: ``NULL`` is a gap, never zero; a damaged value is a storage error.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert (stored_real(None), stored_real(0.0), stored_real(7)) == (None, 0.0, 7.0)
    with pytest.raises(StorageError):
        stored_real("1.5")
    with pytest.raises(StorageError):
        stored_real(True)
    assert row_key((3, 4, 99)) == (3, 4)
    with pytest.raises(StorageError):
        row_key((None, 4))
