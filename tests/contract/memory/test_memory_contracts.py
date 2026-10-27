"""Run every shared contract suite against the in-memory adapter (DB-001..007).

Dependencies:
    tests.contract.contract_*: The suites; fixtures from this directory's conftest.

Example:
    $ uv run pytest tests/contract/memory/test_memory_contracts.py
"""

from tests.contract.contract_acceptance import AcceptanceContract
from tests.contract.contract_auth_sessions import AuthSessionsContract
from tests.contract.contract_auth_users import AuthUsersContract
from tests.contract.contract_dashboards import DashboardsContract
from tests.contract.contract_interface_status import InterfaceStatusContract
from tests.contract.contract_metadata_crud import MetadataCrudContract
from tests.contract.contract_metadata_identity import MetadataIdentityContract
from tests.contract.contract_metadata_lifecycle import MetadataLifecycleContract
from tests.contract.contract_metadata_operations import OperationsContract
from tests.contract.contract_metadata_rules import MetadataRulesContract
from tests.contract.contract_ranking_projection import RankingProjectionContract
from tests.contract.contract_rollup_source import RollupSourceContract
from tests.contract.contract_telemetry_history import TelemetryHistoryContract
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


class TestMemoryMetadataIdentity(MetadataIdentityContract):
    """IFID-007, META-008 transfer, and the change counter on the memory adapter."""


class TestMemoryMetadataLifecycle(MetadataLifecycleContract):
    """LIFE-003 and E.6: the stale clock's start on the memory adapter."""


class TestMemoryRollupSource(RollupSourceContract):
    """RET-004, RET-009, DB-004 rollup reads on the in-memory telemetry store."""


class TestMemoryAuthUsers(AuthUsersContract):
    """SEC-005, SEC-009, AUTH-004: users and login failures on the memory adapter."""


class TestMemoryAuthSessions(AuthSessionsContract):
    """SEC-010, AUTH-002: sessions and kiosk tokens on the memory adapter."""


class TestMemoryDashboards(DashboardsContract):
    """UI-004, DASH-007, DB-003: custom dashboards on the memory adapter."""


class TestMemoryOperations(OperationsContract):
    """API-011, DISC-T03, API-T06: admin operations on the memory adapter."""


class TestMemoryTelemetryHistory(TelemetryHistoryContract):
    """10.6 downsampling, E.17 #13 ranking summaries, MPOS-008 on the in-memory telemetry store."""


class TestMemoryRankingProjection(RankingProjectionContract):
    """E.28, DASH-T01, DASH-T02, MPOS-008: projected-bucket bookkeeping on the memory store."""


class TestMemoryAcceptance(AcceptanceContract):
    """E.35, MVP-ACC-01/02/10 lean reads on the in-memory telemetry store."""


class TestMemoryInterfaceStatus(InterfaceStatusContract):
    """E.6, E.35 interface status records on the in-memory telemetry store."""
