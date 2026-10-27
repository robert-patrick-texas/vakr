"""Run the shared metadata contract suites against the SQLite adapter (DB-001..003, DB-007).

The suites are used unchanged; the fixture comes from this directory's conftest.

Dependencies:
    tests.contract.contract_metadata_crud, contract_metadata_rules, contract_metadata_identity,
    contract_metadata_lifecycle, contract_auth_users, contract_auth_sessions,
    contract_metadata_operations.

Example:
    $ uv run pytest tests/contract/sqlite_metadata/test_sqlite_metadata_contracts.py
"""

from tests.contract.contract_auth_sessions import AuthSessionsContract
from tests.contract.contract_auth_users import AuthUsersContract
from tests.contract.contract_dashboards import DashboardsContract
from tests.contract.contract_metadata_crud import MetadataCrudContract
from tests.contract.contract_metadata_identity import MetadataIdentityContract
from tests.contract.contract_metadata_lifecycle import MetadataLifecycleContract
from tests.contract.contract_metadata_operations import OperationsContract
from tests.contract.contract_metadata_rules import MetadataRulesContract


class TestSqliteMetadataCrud(MetadataCrudContract):
    """DB-001 and DB-007 on a SQLite metadata file."""


class TestSqliteMetadataRules(MetadataRulesContract):
    """DB-002, DB-003, DB-008 (reader beside writer), META-008/009, IFID-005, SEC-018 on SQLite."""


class TestSqliteMetadataIdentity(MetadataIdentityContract):
    """IFID-007, META-008 transfer, and the change counter on a SQLite metadata file."""


class TestSqliteMetadataLifecycle(MetadataLifecycleContract):
    """LIFE-003 and E.6: the stale clock's start in a SQLite metadata file (schema 7)."""


class TestSqliteAuthUsers(AuthUsersContract):
    """SEC-005, SEC-009, AUTH-004: users and login failures on a SQLite metadata file."""


class TestSqliteAuthSessions(AuthSessionsContract):
    """SEC-010, AUTH-002: sessions and kiosk tokens on a SQLite metadata file."""


class TestSqliteDashboards(DashboardsContract):
    """UI-004, DASH-007, DB-003: custom dashboards on a SQLite metadata file."""


class TestSqliteOperations(OperationsContract):
    """API-011, DISC-T03, API-T06: admin operations on a SQLite metadata file."""
