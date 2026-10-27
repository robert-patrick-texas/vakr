"""CLI: ``vakr device add|set|remove|show`` and ``vakr interface annotate|tag`` (27.4, META-013).

Runs the real commands against a relocated install (``VAKR_ROOT``) with a
SQLite metadata database: a device is added with a credential name the
provider defines, changed, cleared, shown, and retired; interfaces are
annotated and tagged; file-managed records and a peer node refuse with
exit status 1 and a message naming the file or the primary. The
credentials file holds a community generated at test time, and no output
may contain it (CRED-002).

Dependencies:
    pytest; vakr.cli.main; the SQLite metadata adapter.

Example:
    $ uv run pytest tests/unit/inventory/test_device_cli.py
"""

import json
import secrets
from pathlib import Path

import pytest

from tests.contract import contract_samples as samples
from vakr.adapters.sqlite.metadata import (
    METADATA_FILE,
    SqliteMetadataStore,
    metadata_migrations,
    sqlite_uow_factory,
)
from vakr.cli.main import main
from vakr.domain.paging import PageRequest
from vakr.inventory.model import FILE_OWNER

COMMUNITY = f"fake-{secrets.token_hex(8)}"
"""Generated when the tests run, never stored; no output may contain it (CRED-002)."""


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Relocate the install with a credentials file and a migrated metadata database.

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
    metadata_migrations(tmp_path / "var/lib/vakr" / METADATA_FILE).migrate()
    return tmp_path


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
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


def store(root: Path) -> SqliteMetadataStore:
    """Open the install's metadata database.

    Args:
        root: Install root.

    Returns:
        SqliteMetadataStore: The store; the caller closes it.

    Raises:
        None
    """
    return SqliteMetadataStore(root / "var/lib/vakr" / METADATA_FILE)


def test_27_4_device_add_set_show_remove(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """27.4, META-009, SEC-018: the CLI writes through the service, audited as cli:<login>.

    Args:
        root: Install root.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    code, text = run(capsys, "device", "add", "edge9.example.net", "192.0.2.9", "--credential",
                     "network-v2c", "--tags", "wan,edge", "--interval", "2m", "--dev")  # fmt: skip
    assert code == 0, text
    assert "API-managed, version 1" in text
    assert "changed: created" in text
    code, text = run(capsys, "device", "add", "sw9", "192.0.2.9", "--credential", "nope", "--dev")
    assert code == 1
    assert "credential: no SNMP credential named 'nope'" in text
    assert "already polled" in text
    code, text = run(capsys, "device", "set", "edge9.example.net", "--interval", "",
                     "--comment", "lab", "--dev")  # fmt: skip
    assert code == 0, text
    assert "changed: interval_s: 120 -> none" in text
    assert "version 2" in text
    code, text = run(capsys, "device", "show", "edge9.example.net", "--json")
    record = json.loads(text)
    assert (record["interval"], record["tags"], record["comment"]) == (None, ["edge", "wan"], "lab")
    code, text = run(capsys, "device", "remove", record["id"])
    assert (code, "retired" in text) == (0, True)
    assert run(capsys, "device", "show", "nowhere")[0] == 1
    assert run(capsys, "device")[0] == 2
    opened = store(root)
    try:
        with sqlite_uow_factory(opened)() as uow:
            events = uow.audit.list_page(PageRequest(20)).items
    finally:
        opened.close()
    assert sorted(e.action for e in events) == ["device.create", "device.retire", "device.update"]
    assert all(e.actor.startswith("cli:") for e in events)


def test_meta013_interface_annotate_and_tag(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """META-007, META-011, META-013, META-008: annotate, tag, and refuse a claimed interface.

    Args:
        root: Install root.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    opened = store(root)
    try:
        with sqlite_uow_factory(opened)() as uow:
            device = uow.devices.add(samples.device("edge1", poll_address="192.0.2.10"))
            for n, name in enumerate(("Gi0/0/0", "Gi0/0/1")):
                uow.interfaces.add(samples.interface(device.id, name, if_index=n + 1))
            uow.interfaces.add(samples.interface(device.id, "Gi0/0/9", if_index=9,
                                                 managed_by=FILE_OWNER))  # fmt: skip
            uow.commit()
    finally:
        opened.close()
    code, text = run(capsys, "interface", "annotate", "edge1", "gi0/0/0", "--high-priority",
                     "--wan-circuit", "--provider", "Example Carrier", "--contracted-bps",
                     "100000000", "--tags", "uplink")  # fmt: skip
    assert code == 0, text
    assert "interest: primary" in text
    assert "contracted_bps: 100000000" in text
    code, text = run(capsys, "interface", "annotate", "edge1", "Gi0/0/0", "--provider", "",
                     "--json")  # fmt: skip
    assert (code, json.loads(text)["provider"]) == (0, None)
    code, text = run(capsys, "interface", "tag", "edge1", "Gi0/0/0", "Gi0/0/1", "--add", "core")
    assert (code, text.strip()) == (0, "tagged 2 interface(s), 0 already so")
    code, text = run(capsys, "interface", "tag", "edge1", "Gi0/0/1", "Gi0/0/9", "--add", "x")
    assert code == 1
    assert "managed by inventory.toml" in text
    assert run(capsys, "interface", "annotate", "edge1", "Gi9/9", "--sticky")[0] == 1
    assert run(capsys, "interface", "tag", "nowhere", "Gi0/0/0", "--add", "x")[0] == 1


def test_mpos003_cli_edits_refuse_on_a_peer(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """MPOS-003, MPOS-T03: every CLI edit refuses on a peer, naming the primary.

    Args:
        root: Install root.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    config = root / "etc/vakr/config.toml"
    config.write_text('[node]\nrole = "peer"\nposition = "west"\n'
                      'primary_url = "https://vakr-east.example.net"\n')  # fmt: skip
    for argv in (("device", "add", "edge9", "192.0.2.9"), ("device", "remove", "edge9"),
                 ("interface", "tag", "edge1", "Gi0/0/0", "--add", "x")):  # fmt: skip
        code, text = run(capsys, *argv)
        assert code == 1, argv
        assert "vakr-east.example.net" in text, argv
