"""The observation replication endpoints as ``vakr-api`` serves them (MPOS-004, MPOS-006).

The app is built as the service builds itself (``build_parts``) over a
node whose collector identity file names one collector and whose telemetry
store logged that collector's observations. A ``vkr_`` token reads the state
and the pages; another collector is a 404; operators, kiosks, and anonymous
callers never reach the endpoints; the health line names replication on a
node with a partner.

Dependencies:
    pytest, fastapi.testclient; tests.unit.replication.obs_testkit.

Example:
    $ uv run pytest tests/unit/replication/test_observation_api.py
"""

import json
from collections.abc import Generator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from tests.unit.auth.auth_testkit import cheap_hasher
from tests.unit.replication import obs_testkit as k
from tests.unit.replication.repl_testkit import open_store, seed
from vakr.api.app import create_app
from vakr.app.api.build import ApiParts, build_parts
from vakr.app.collector.identity import identity_path
from vakr.auth import Passwords
from vakr.domain.envelope import envelope_from_dict, open_envelope
from vakr.domain.ids import BatchId, CollectorId, new_id
from vakr.paths import InstallPaths
from vakr.ports.clock import ManualClock
from vakr.replication import observation_status as rec


@pytest.fixture
def node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[ApiParts, UUID]]:
    """Build the API over a node with a collector identity and logged observations.

    Args:
        tmp_path: The install root.
        monkeypatch: ``VAKR_ROOT``.

    Returns:
        Generator[tuple]: The parts and the local collector; closed afterwards.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    paths = InstallPaths.under(tmp_path)
    for directory in (paths.config_dir, paths.state_dir, paths.run_dir):
        directory.mkdir(parents=True, exist_ok=True)
    store, factory = open_store(paths.state_dir / "metadata.db")
    seed(factory, ManualClock(k.T0))
    store.close()
    east = new_id(CollectorId)
    identity_path(paths.state_dir).parent.mkdir(parents=True)
    identity_path(paths.state_dir).write_text(
        json.dumps({"format": "vakr-collector-identity", "collector_id": str(east)})
    )
    telemetry = k.store_at(paths.state_dir)
    k.backend(telemetry).writer.write_batch(new_id(BatchId), k.minutes(east, 2))
    telemetry.close()
    (paths.config_dir / "config.toml").write_text(
        'config_version = 1\n\n[api]\nlisten_address = ""\n\n[node]\nrole = "primary"\n'
        'position = "east"\npeer_url = "https://west.example"\n'
    )
    built = build_parts(paths, clock=ManualClock(k.T0), passwords=Passwords(cheap_hasher()))
    yield built, east
    built.close()


def test_mpos004_a_partner_reads_the_state_and_pages_with_its_token(
    node: tuple[ApiParts, UUID],
) -> None:
    """MPOS-004, MPOS-006: the state lists this node's collector; pages are envelopes, in order.

    Args:
        node: The API parts and the local collector.

    Returns:
        None

    Raises:
        None
    """
    parts, east = node
    token = parts.context.auth.replication.create("west", actor="test").token.reveal()
    bearer = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(parts.context), base_url="http://127.0.0.1") as client:
        state = client.get("/api/v1/replication/observations/state", headers=bearer)
        assert state.status_code == 200, state.text
        found = state.json()["collectors"]
        assert [(c["id"], c["oldest_seq"], c["newest_seq"]) for c in found] == [(str(east), 1, 10)]
        page = client.get(
            "/api/v1/replication/observations",
            params={"collector": str(east), "after": 4, "limit": 3},
            headers=bearer,
        ).json()
        assert [o["seq"] for o in page["observations"]] == [5, 6, 7]
        assert (page["next_after"], page["has_more"]) == (7, True)
        first = open_envelope(envelope_from_dict(page["observations"][0]["envelope"]))
        assert first.collector_id == east
        other = client.get(
            "/api/v1/replication/observations",
            params={"collector": str(new_id(CollectorId))},
            headers=bearer,
        )
        assert other.status_code == 404
        too_many = client.get(
            "/api/v1/replication/observations",
            params={"collector": str(east), "limit": 10_001},
            headers=bearer,
        )
        assert too_many.status_code == 422
        assert client.get("/api/v1/replication/observations/state").status_code == 401
        kiosk = parts.context.auth.kiosk.create("wall", actor="test").token.reveal()
        refused = client.get(
            "/api/v1/replication/observations/state", headers={"Authorization": f"Bearer {kiosk}"}
        )
        assert refused.status_code == 401
        assert state.headers["cache-control"] == "no-store"


def test_mpos006_the_health_line_names_replication_on_a_node_with_a_partner(
    node: tuple[ApiParts, UUID],
) -> None:
    """27.5 item 7, MPOS-006: the device list's health carries replication; never device state.

    Args:
        node: The API parts and the local collector.

    Returns:
        None

    Raises:
        None
    """
    parts, _east = node
    health = parts.context.overview.device_list().health
    assert health.replication is not None
    assert health.replication.text == "no observation pulled from the other position yet"
    paths = InstallPaths.from_environment()
    stamp = rec.stamp(k.T0)
    rec.write_status(
        paths.state_dir / rec.FILE,
        rec.ObservationPullStatus(
            last_attempt_at=stamp,
            last_success_at=stamp,
            sources={
                "w": rec.SourceStatus(position="west", cursor=4, newest_seq=6, newest_ts=stamp)
            },
        ),
    )
    again = parts.context.overview.device_list().health.replication
    assert again is not None
    assert again.text.startswith("west ")
    assert "backlog 2" in again.text
    assert again.value is not None
