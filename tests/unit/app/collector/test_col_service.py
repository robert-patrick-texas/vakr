"""The collector service lifecycle: preflight, start, stop, reload (SVC-T02, SVC-T03, TEST-008).

Each test builds a real relocated install in ``tmp_path`` (configuration,
migrated SQLite databases, spool), with the inventory seam pointing at an
empty or lab inventory, so no network is needed.

Dependencies:
    tests.integration.collector.collector_lab: The install and lab inventory.
    vakr.app.collector: Build, service, entry, identity.

Example:
    $ uv run pytest tests/unit/app/collector/test_col_service.py
"""

import asyncio
import itertools
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from tests.integration.collector.collector_lab import make_root
from tests.unit.adapters.sqlite.sqlite_testkit import NEXT, family_with, synthetic
from tests.unit.credentials.credentials_testkit import (
    FakeSecrets,
    Install,
    own_policy,
    v2c_table,
)
from vakr.adapters.sqlite.metadata import SqliteMetadataStore, sqlite_uow_factory
from vakr.adapters.sqlite.migrations import SqliteMigrationManager
from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE, SqliteTelemetry, SqliteTelemetryStore
from vakr.app.collector import inventory as inventory_seam
from vakr.app.collector import preflight
from vakr.app.collector.build import build_parts
from vakr.app.collector.checkpoint import save_checkpoint
from vakr.app.collector.entry import run_collector
from vakr.app.collector.identity import (
    IdentityConflictError,
    ensure_collector,
    identity_path,
    load_identity,
    resolve_identity,
)
from vakr.app.collector.preflight import PreflightError
from vakr.app.collector.service import CollectorService
from vakr.app.collector.timings import EX_PREFLIGHT, ServiceTimings
from vakr.credentials import FileCredentialProvider
from vakr.domain.enums import ObservationType
from vakr.domain.paging import PageRequest
from vakr.domain.series import TimeWindow
from vakr.paths import InstallPaths

FAST = ServiceTimings(checkpoint_interval_s=0.2, health_interval_s=0.2, change_poll_s=0.1)


def root(tmp_path: Path, extra: str = "") -> InstallPaths:
    """Create a relocated install with migrated databases.

    Args:
        tmp_path: Test directory.
        extra: More configuration.

    Returns:
        InstallPaths: The layout.

    Raises:
        None
    """
    return make_root(tmp_path, 161, extra)


def test_empty_inventory_starts_idles_and_stops_cleanly(tmp_path: Path) -> None:
    """SVC-T03, PKG-003: no devices is not an error; stop spools health and checkpoints.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    paths = root(tmp_path)

    async def scenario() -> CollectorService:
        """Run for a moment, then stop.

        Args:
            None

        Returns:
            CollectorService: The stopped service.

        Raises:
            None
        """
        service = CollectorService(await build_parts(paths), FAST)
        task = asyncio.create_task(service.run())
        await asyncio.sleep(0.7)
        service.request_stop()
        assert await asyncio.wait_for(task, 20) == 0
        return service

    service = asyncio.run(scenario())
    assert service.failed is None
    assert service.parts.checkpoint.is_file()
    store = SqliteTelemetryStore(paths.state_dir / TELEMETRY_FILE)
    latest = SqliteTelemetry(store).collector_health.latest(service.parts.collector.id)
    store.close()
    assert latest is not None
    assert latest.active_devices == 0


def test_a_failing_component_stops_the_service_with_status_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SVC-009: an unexpected component failure shuts down in order and exits 1.

    Args:
        tmp_path: Test directory.
        monkeypatch: Breaks the telemetry upkeep loop.

    Returns:
        None

    Raises:
        None
    """
    paths = root(tmp_path)

    async def broken() -> None:
        """Fail like a bug would.

        Args:
            None

        Returns:
            None

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("bug")

    async def scenario() -> tuple[int, str | None]:
        """Run until the broken part fails.

        Args:
            None

        Returns:
            tuple[int, str | None]: Exit status and the failed component.

        Raises:
            None
        """
        service = CollectorService(await build_parts(paths), FAST)
        monkeypatch.setattr(service.upkeep, "run", broken)
        code = await asyncio.wait_for(service.run(), 20)
        return code, service.failed

    assert asyncio.run(scenario()) == (1, "telemetry upkeep")


def test_reload_refuses_an_invalid_candidate_and_applies_a_valid_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CONF-003, TEST-008: a bad file keeps the running settings; a good one applies live.

    Args:
        tmp_path: Test directory.
        monkeypatch: Wires the inventory reload seam.

    Returns:
        None

    Raises:
        None
    """
    paths = root(tmp_path)
    config = paths.config_dir / "config.toml"

    async def scenario() -> None:
        """Reload twice without running the loop.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        parts = await build_parts(paths)
        service = CollectorService(parts, FAST)
        original = service.icmp_step.collector.settings
        config.write_text(config.read_text() + "\n[icmp]\nnot_an_option = 1\n")
        assert not await service.reload()
        assert service.icmp_step.collector.settings is original
        text = config.read_text().replace("probes = 2", "probes = 3").split("\n[icmp]\nnot_")[0]
        config.write_text(text + "\n[rates]\nmax_gap_intervals = 4.0\n")
        seen: list[object] = []

        def remember(_inventory: object, cfg: object, _config_dir: Path) -> None:
            """Record the reload, as the real inventory's policy swap would.

            Args:
                _inventory: The source.
                cfg: The new configuration.
                _config_dir: Where ``policies.toml`` lives.

            Returns:
                None

            Raises:
                None
            """
            seen.append(cfg)

        monkeypatch.setattr(inventory_seam, "INVENTORY_RELOAD", remember)
        assert await service.reload()
        assert service.icmp_step.collector.settings.probes == 3
        assert service.snmp_step.rates.max_gap_intervals == 4.0
        assert seen == [service.parts.config]

        def refuse(_inventory: object, _cfg: object, _config_dir: Path) -> None:
            """Fail like a policy the inventory cannot use.

            Args:
                _inventory: The source.
                _cfg: The new configuration.
                _config_dir: Where ``policies.toml`` lives.

            Returns:
                None

            Raises:
                ValueError: Always.
            """
            raise ValueError("bad policy")

        monkeypatch.setattr(inventory_seam, "INVENTORY_RELOAD", refuse)
        assert await service.reload()  # collection goes on with the previous policy
        await parts.spool.close()
        await parts.close()

    asyncio.run(scenario())


def test_preflight_refuses_unsafe_starts_before_any_poll(tmp_path: Path) -> None:
    """SVC-T02, SVC-005, MIG-004: bad config, missing state, newer schema, held spool.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    paths = root(tmp_path, "\n[spool]\nnot_an_option = 1\n")
    with pytest.raises(PreflightError) as bad:
        asyncio.run(build_parts(paths))
    assert bad.value.check == "config"
    assert "vakr config validate" in bad.value.message
    with pytest.raises(PreflightError) as missing:
        preflight.check_state_dir(tmp_path / "nowhere")
    assert missing.value.check == "state_dir"
    with pytest.raises(PreflightError) as no_db:
        preflight.open_metadata(tmp_path / "empty")
    assert no_db.value.check == "metadata_db"

    later = family_with(synthetic(NEXT, "add_probe", "CREATE TABLE probe (x INTEGER);"))
    SqliteMigrationManager(paths.state_dir / "metadata.db", later).migrate()
    with pytest.raises(PreflightError) as newer:
        preflight.open_metadata(paths.state_dir)
    assert "MIG-004" in newer.value.message


def test_one_identity_one_process(tmp_path: Path) -> None:
    """16.3, MPOL-006: the identity is stable, and a second process cannot take the spool.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    paths = root(tmp_path)

    async def scenario() -> None:
        """Build twice while the first holds the spool.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        first = await build_parts(paths)
        with pytest.raises(PreflightError) as held:
            await build_parts(paths)
        assert held.value.check == "spool"
        assert any("no credentials file" in w for w in first.warnings)
        await first.spool.close()
        again = await build_parts(paths)
        assert again.collector.id == first.collector.id
        await again.spool.close()
        await first.close()
        await again.close()

    asyncio.run(scenario())
    store = SqliteMetadataStore(paths.state_dir / "metadata.db")
    uow = sqlite_uow_factory(store)
    a = ensure_collector(uow, None, "other", "west")
    assert ensure_collector(uow, a.id, "other", "east") == a  # never changed once registered
    store.close()


def test_entry_reports_a_failed_preflight_and_exits_78(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """SVC-T02, SVC-T05: one operator sentence on stderr, and the no-restart exit status.

    Args:
        tmp_path: Test directory.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    paths = InstallPaths.under(tmp_path / "nothing")
    paths.config_dir.mkdir(parents=True)
    assert asyncio.run(run_collector(paths)) == EX_PREFLIGHT
    err = capsys.readouterr().err
    assert "<3>[collector.preflight_failed]" in err
    assert "state_dir" in err


def test_registration_and_health_cadence(tmp_path: Path) -> None:
    """MPOL-006, OBS-001: one persisted UUID everywhere; health at once, then every interval.

    The views read a collector as unavailable when its newest health
    observation is older than the E.3 threshold, so the first one is written
    at start and later ones at least once per poll interval.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    paths = root(tmp_path)
    timings = ServiceTimings(health_interval_s=0.5, change_poll_s=0.1)

    async def scenario() -> tuple[CollectorService, datetime]:
        """Run for about two seconds.

        Args:
            None

        Returns:
            tuple[CollectorService, datetime]: The stopped service and its start time.

        Raises:
            None
        """
        service = CollectorService(await build_parts(paths), timings)
        began = datetime.now(UTC)
        task = asyncio.create_task(service.run())
        await asyncio.sleep(1.8)
        service.request_stop()
        assert await asyncio.wait_for(task, 20) == 0
        return service, began

    service, began = asyncio.run(scenario())
    cid = service.parts.collector.id
    assert load_identity(identity_path(paths.state_dir)) == cid
    assert service.parts.spool.directory.name == str(cid)
    meta = SqliteMetadataStore(paths.state_dir / "metadata.db")
    with sqlite_uow_factory(meta)() as uow:
        record = uow.collectors.get(cid)
    meta.close()
    assert record is not None
    assert record.name == "e2e-east"
    store = SqliteTelemetryStore(paths.state_dir / TELEMETRY_FILE)
    window = TimeWindow(began - timedelta(minutes=1), datetime.now(UTC) + timedelta(minutes=1))
    page = SqliteTelemetry(store).query.read_raw(
        ObservationType.COLLECTOR_HEALTH, cid, window, PageRequest(limit=100), collector_id=cid
    )
    store.close()
    times = [o.timestamp for o in page.items]
    assert len(times) >= 4
    assert times[0] - began < timedelta(seconds=0.5)  # written at start, not a minute later
    gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(times)]
    assert max(gaps) < 0.9
    service.replanner.shortest_interval_s = 30
    assert service.health_interval_s() == 0.5
    service.timings = ServiceTimings()
    assert service.health_interval_s() == 30  # at least once per (shortest) poll interval


def test_identity_survives_and_conflicts_are_refused(tmp_path: Path) -> None:
    """MPOL-006: restarts keep the UUID; a name registered under another UUID is refused.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    paths = root(tmp_path)
    meta = SqliteMetadataStore(paths.state_dir / "metadata.db")
    uow = sqlite_uow_factory(meta)
    first = resolve_identity(uow, paths.state_dir, "east", "east")
    assert resolve_identity(uow, paths.state_dir, "east", "east") == first
    other = tmp_path / "other-disk"
    assert resolve_identity(uow, other, "east", "east").id == first.id  # adopted by name
    stranger = {"format": "vakr-collector-identity", "collector_id": str(uuid4())}
    save_checkpoint(stranger, identity_path(other))
    with pytest.raises(IdentityConflictError):
        resolve_identity(uow, other, "east", "east")
    identity_path(other).write_text("{}")
    with pytest.raises(ValueError, match="identity file"):
        resolve_identity(uow, other, "east", "east")
    meta.close()


def test_unsafe_or_invalid_credentials_stop_the_start(tmp_path: Path) -> None:
    """SVC-T02, SEC-016: an unsafe or invalid credentials file fails the preflight, valueless.

    Args:
        tmp_path: Test directory.

    Returns:
        None

    Raises:
        None
    """
    fake = FakeSecrets.generate()
    layout = Install.under(tmp_path)
    layout.write(v2c_table("lab-v2c", fake.community), mode=0o644)
    provider = FileCredentialProvider(layout.secrets_dir, policy=own_policy())
    with pytest.raises(PreflightError) as unsafe:
        preflight.check_credentials(provider)
    assert unsafe.value.check == "credentials"
    assert "unsafe" in unsafe.value.message
    assert fake.community not in str(unsafe.value)
    layout.write("[credentials.lab-v2c]\nversion = 7\n", mode=0o640)
    with pytest.raises(PreflightError) as invalid:
        preflight.check_credentials(provider)
    assert "invalid" in invalid.value.message
    layout.write(v2c_table("lab-v2c", fake.community), mode=0o640)
    assert preflight.check_credentials(provider) is None
    provider.forget()
