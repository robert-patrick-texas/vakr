"""Run the shared telemetry contract suites against the SQLite adapter (DB-004..007).

The suites are used unchanged; the fixture comes from this directory's conftest.

Dependencies:
    tests.contract.contract_telemetry_series, contract_telemetry_state, contract_telemetry_write.

Example:
    $ uv run pytest tests/contract/sqlite_telemetry/test_sqlite_telemetry_contracts.py
"""

from tests.contract.contract_rollup_source import RollupSourceContract
from tests.contract.contract_telemetry_series import TelemetrySeriesContract
from tests.contract.contract_telemetry_state import TelemetryStateContract
from tests.contract.contract_telemetry_write import TelemetryWriteContract


class TestSqliteTelemetryWrite(TelemetryWriteContract):
    """DB-004, STORE-009/010/013 on a SQLite telemetry file."""


class TestSqliteTelemetrySeries(TelemetrySeriesContract):
    """STORE-012, API-006, STATE-004/005, DB-006, RET-007/009 on a SQLite telemetry file."""


class TestSqliteTelemetryState(TelemetryStateContract):
    """DB-005, STATE-003, OBS-001, DB-007 on a SQLite telemetry file."""


class TestSqliteRollupSource(RollupSourceContract):
    """RET-004, RET-009, DB-004 rollup reads on the SQLite telemetry store."""
