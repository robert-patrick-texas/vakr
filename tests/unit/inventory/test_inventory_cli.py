"""CLI: ``vakr inventory`` and ``vakr interface`` exit codes and output (CFG-008, CFG-010).

Runs the real commands against a relocated install (``VAKR_ROOT``) with a
SQLite metadata database: validate/plan/apply/list, the claim, review
list/map/new, and the ``vakr config validate`` inventory rule. The
credentials file holds a community generated at test time, and no output
may contain it (CRED-002).

Dependencies:
    pytest; vakr.cli.main; the SQLite metadata adapter.

Example:
    $ uv run pytest tests/unit/inventory/test_inventory_cli.py
"""

import json
import secrets
from pathlib import Path

import pytest

from tests.unit.inventory.inventory_fixtures import (
    DAY,
    SAMPLE_INVENTORY,
    T0,
    discovery,
    row,
    write_inventory,
)
from vakr.adapters.sqlite.metadata import (
    METADATA_FILE,
    SqliteMetadataStore,
    metadata_migrations,
    sqlite_uow_factory,
)
from vakr.cli.main import main
from vakr.domain.ids import CollectorId, new_id
from vakr.inventory.service import InventoryService
from vakr.policy.rules import Policy
from vakr.ports.clock import ManualClock

COMMUNITY = f"fake-{secrets.token_hex(8)}"
"""Generated when the tests run, never stored; no output may contain it (CRED-002)."""


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Relocate the install and give it a credentials file with a fresh fake community.

    Args:
        tmp_path: Install root.
        monkeypatch: Sets ``VAKR_ROOT``.

    Returns:
        Path: The install root.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    secrets_dir = tmp_path / "etc/vakr/secrets"
    secrets_dir.mkdir(parents=True, mode=0o750)
    creds = secrets_dir / "credentials.toml"
    creds.write_text(f'[credentials.network-v2c]\nversion = "2c"\ncommunity = "{COMMUNITY}"\n')
    creds.chmod(0o640)
    (tmp_path / "var/lib/vakr").mkdir(parents=True)
    return tmp_path


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    """Run ``vakr`` and return its exit code and combined output.

    Args:
        capsys: Captures output.
        *argv: Arguments.

    Returns:
        tuple[int, str]: Exit code and stdout plus stderr.

    Raises:
        None
    """
    code = main(list(argv))
    out = capsys.readouterr()
    text = out.out + out.err
    assert COMMUNITY not in text  # CRED-002: never print a secret
    return code, text


def test_cfg008_validate_plan_apply_list(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CFG-008, CFG-010: documented exit codes; plan without a database; apply needs one.

    Args:
        root: Install root.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    assert _run(capsys, "inventory", "validate")[0] == 0  # no file: nothing to validate
    write_inventory(root / "etc/vakr")
    code, text = _run(capsys, "inventory", "validate", "--dev")
    assert code == 0
    assert "inventory.toml: valid (1 site(s), 2 device(s), 1 annotation(s))" in text
    code, text = _run(capsys, "inventory", "plan", "--dev")
    assert code == 0
    assert "everything would be created" in text
    assert _run(capsys, "inventory", "apply", "--dev")[0] == 4  # no metadata database yet
    metadata_migrations(root / "var/lib/vakr" / METADATA_FILE).migrate()
    code, text = _run(capsys, "inventory", "apply", "--dev", "--json")
    data = json.loads(text[text.index("{") :])
    assert code == 0
    assert data["applied"]
    assert data["summary"] == "3 create"
    code, text = _run(capsys, "inventory", "apply", "--dev")
    assert code == 0
    assert "nothing to apply" in text
    code, text = _run(capsys, "inventory", "list")
    assert code == 0
    assert "edge1.example.net" in text
    assert "file:inventory.toml" in text
    assert _run(capsys, "inventory")[0] == 2


def test_invalid_file_exits_1_with_located_messages(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """11.4, NETSEC-001: problems name file and path; exit status 1.

    Args:
        root: Install root.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    write_inventory(root / "etc/vakr", SAMPLE_INVENTORY.replace("192.0.2.20", "169.254.169.254"))
    code, text = _run(capsys, "inventory", "plan", "--dev")
    assert code == 1
    assert "inventory.toml: devices[1].address: 169.254.169.254 is denied by" in text
    code, text = _run(capsys, "config", "validate")
    assert code == 1
    assert "invalid inventory file" in text
    assert "devices[1].address" in text


def test_e19_claim_and_review_commands(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """E.19, IFID-T07: claim through the CLI; list, map, and create through review commands.

    Args:
        root: Install root.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    write_inventory(root / "etc/vakr")
    db = root / "var/lib/vakr" / METADATA_FILE
    metadata_migrations(db).migrate()
    assert _run(capsys, "inventory", "apply", "--dev")[0] == 0
    store = SqliteMetadataStore(db)
    factory = sqlite_uow_factory(store)
    service = InventoryService(factory, new_id(CollectorId), Policy(), ManualClock(T0))
    edge = next(d for d in service.devices() if d.name == "edge1.example.net")
    service.record_discovery(edge.id, discovery([row(1, "Gi0/0/0"), row(5, "Gi0/0/5")]), T0)
    service.record_discovery(edge.id, discovery([row(1, "Gi0/0/0"), row(5, "Te0/0/5"),
                                                 row(6, "Te0/0/6")]), T0 + DAY)  # fmt: skip
    store.close()
    code, text = _run(capsys, "interface", "claim", "edge1.example.net", "Gi0/0/0", "--dev")
    assert code == 0
    assert "claimed edge1.example.net Gi0/0/0" in text
    assert _run(capsys, "interface", "claim", "edge1.example.net", "Gi0/0/0", "--dev")[0] == 1
    code, text = _run(capsys, "interface", "review", "list", "--json")
    (review,) = json.loads(text)["reviews"]
    assert (review["if_index"], review["outcome"]) == (5, "probable")
    code, text = _run(capsys, "interface", "review", "map", review["id"], review["candidates"][0])
    assert code == 0
    assert "mapped to" in text
    assert _run(capsys, "interface", "review", "new", review["id"])[0] == 1  # already closed
    code, text = _run(capsys, "interface", "review", "list")
    assert code == 0
    assert "no open identity reviews" in text
    assert _run(capsys, "interface", "review")[0] == 2
