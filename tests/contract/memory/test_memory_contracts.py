"""Run every shared contract suite against the in-memory adapter (DB-001..007).

Dependencies:
    tests.contract.contract_*: The suites; fixtures from this directory's conftest.

Example:
    $ uv run pytest tests/contract/memory/test_memory_contracts.py
"""

from tests.contract.contract_metadata_crud import MetadataCrudContract
from tests.contract.contract_metadata_rules import MetadataRulesContract
from tests.contract.contract_telemetry_series import TelemetrySeriesContract
from tests.contract.contract_telemetry_state import TelemetryStateContract
from tests.contract.contract_telemetry_write import TelemetryWriteContract


class TestMemoryMetadataCrud(MetadataCrudContract):
    """DB-001 and DB-007 on the in-memory metadata store."""


class TestMemoryMetadataRules(MetadataRulesContract):
    """DB-002, DB-003, META-008/009, IFID-005, SEC-018 on the in-memory metadata store."""


class TestMemoryTelemetryWrite(TelemetryWriteContract):
    """DB-004, STORE-009/010 on the in-memory telemetry store."""


class TestMemoryTelemetrySeries(TelemetrySeriesContract):
    """STORE-012, API-006, DB-006, retention on the in-memory telemetry store."""


class TestMemoryTelemetryState(TelemetryStateContract):
    """DB-005, OBS-001, DB-007 on the in-memory telemetry store."""
