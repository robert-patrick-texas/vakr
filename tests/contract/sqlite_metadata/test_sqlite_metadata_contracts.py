"""Run the shared metadata contract suites against the SQLite adapter (DB-001..003, DB-007).

The suites are used unchanged; the fixture comes from this directory's conftest.

Dependencies:
    tests.contract.contract_metadata_crud, contract_metadata_rules.

Example:
    $ uv run pytest tests/contract/sqlite_metadata/test_sqlite_metadata_contracts.py
"""

from tests.contract.contract_metadata_crud import MetadataCrudContract
from tests.contract.contract_metadata_identity import MetadataIdentityContract
from tests.contract.contract_metadata_rules import MetadataRulesContract


class TestSqliteMetadataCrud(MetadataCrudContract):
    """DB-001 and DB-007 on a SQLite metadata file."""


class TestSqliteMetadataRules(MetadataRulesContract):
    """DB-002, DB-003, DB-008 (reader beside writer), META-008/009, IFID-005, SEC-018 on SQLite."""


class TestSqliteMetadataIdentity(MetadataIdentityContract):
    """IFID-007, META-008 transfer, and the change counter on a SQLite metadata file."""
