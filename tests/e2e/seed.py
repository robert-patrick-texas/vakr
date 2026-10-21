"""Build a relocated vakr install with the end-to-end fleet and two accounts (WEB-T01 to WEB-T07).

``build_install`` lays out an install under one directory exactly as the
package does (``VAKR_ROOT``): configuration with an ``[api]`` listener on
loopback, the display timezone ``America/Chicago`` (``time.timezone``,
TIME-003, WEB-T06), and ``east`` as the local collector
(``scheduler.collector_name``, E.28 #5); migrated databases; the fleet of
``tests.e2e.fleet``; and accounts ``e2e-admin`` (admin) and
``e2e-operator`` (operator), created through ``UserAdmin`` as ``vakr user
add`` does.

Scenarios:

- ``normal``: metadata and telemetry as described in ``tests.e2e.telemetry``.
- ``no-telemetry``: metadata only, and no ``telemetry.db`` at all, so every
  telemetry view answers ``available: false`` and devices read "Unknown —
  telemetry unavailable", never Down (UI-014, UI-015, WEB-T07).

Dependencies:
    vakr.adapters.sqlite: Migrations and stores.
    vakr.auth: ``UserAdmin`` and password hashing.
    tests.e2e: The fleet, metadata, and telemetry seeds.

Safety:
    Passwords arrive as arguments (the Playwright config generates them per
    run), go straight into ``Secret``, and are never printed or written;
    the fixtures file holds names and UUIDs only.

Example:
    >>> SCENARIOS
    ('normal', 'no-telemetry')
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tests.e2e.metadata import Seeded, seed_metadata
from tests.e2e.telemetry import LiveCollector
from vakr.adapters.sqlite.metadata import (
    METADATA_FILE,
    SqliteMetadataStore,
    metadata_migrations,
    sqlite_uow_factory,
)
from vakr.adapters.sqlite.telemetry import TELEMETRY_FILE, telemetry_migrations
from vakr.auth.accounts import UserAdmin
from vakr.auth.passwords import Passwords
from vakr.auth.settings import auth_settings_from_config
from vakr.config.loader import load_effective_config
from vakr.credentials.secret import Secret
from vakr.domain.users import Role
from vakr.paths import InstallPaths
from vakr.ports.clock import SystemClock

SCENARIOS = ("normal", "no-telemetry")
ADMIN_USER, OPERATOR_USER = "e2e-admin", "e2e-operator"
DISPLAY_ZONE = "America/Chicago"
CONFIG = """config_version = 1

[api]
listen_address = "127.0.0.1"
port = {port}

[time]
timezone = "{zone}"

[scheduler]
collector_name = "east"
"""


@dataclass
class Install:
    """A seeded install.

    Attributes:
        paths: Its layout.
        seeded: The stored fleet.
        anchor: The seed's "now", aligned down to the minute (UTC).
        collector: Writes fresh polls; ``None`` in the no-telemetry scenario.
    """

    paths: InstallPaths
    seeded: Seeded
    anchor: datetime
    collector: LiveCollector | None


def anchor_now() -> datetime:
    """Return the current time aligned down to the minute.

    Args:
        None

    Returns:
        datetime: UTC.

    Raises:
        None
    """
    return datetime.now(UTC).replace(second=0, microsecond=0)


def lay_out(root: Path, port: int, *, telemetry: bool) -> InstallPaths:
    """Create the directories, the configuration, and migrated databases.

    Args:
        root: The install prefix (``VAKR_ROOT``).
        port: TCP port of the API.
        telemetry: False to leave ``telemetry.db`` out entirely.

    Returns:
        InstallPaths: The layout.

    Raises:
        None
    """
    paths = InstallPaths.under(root)
    for directory in (paths.config_dir, paths.state_dir, paths.run_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (paths.config_dir / "config.toml").write_text(CONFIG.format(port=port, zone=DISPLAY_ZONE))
    metadata_migrations(paths.state_dir / METADATA_FILE).migrate()
    if telemetry:
        telemetry_migrations(paths.state_dir / TELEMETRY_FILE).migrate()
    return paths


def create_accounts(paths: InstallPaths, passwords: dict[str, str], hashing: Passwords) -> None:
    """Create the admin and operator accounts, as ``vakr user add`` does.

    Args:
        paths: The install.
        passwords: Password by username (``e2e-admin``, ``e2e-operator``).
        hashing: Password hashing; production parameters for a real server.

    Returns:
        None

    Raises:
        ValidationError: If a password breaks the policy (at least 15 characters).
    """
    config = load_effective_config(paths.config_dir)
    store = SqliteMetadataStore(paths.state_dir / METADATA_FILE)
    try:
        admin = UserAdmin(sqlite_uow_factory(store), SystemClock(), hashing,
                          auth_settings_from_config(config))  # fmt: skip
        for name, role in ((ADMIN_USER, Role.ADMIN), (OPERATOR_USER, Role.OPERATOR)):
            admin.create(name, role, Secret(passwords[name]), actor="e2e-seed",
                         display_name=f"E2E {role.value}")  # fmt: skip
    finally:
        store.close()


def build_install(
    root: Path,
    port: int,
    scenario: str,
    passwords: dict[str, str],
    hashing: Passwords | None = None,
) -> Install:
    """Build and seed a whole install.

    Args:
        root: The install prefix.
        port: TCP port of the API.
        scenario: One of ``SCENARIOS``.
        passwords: Password by username.
        hashing: Password hashing; ``None`` means production parameters.

    Returns:
        Install: The seeded install; close ``collector`` when done.

    Raises:
        ValueError: For an unknown scenario.
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; choose from {', '.join(SCENARIOS)}")
    telemetry = scenario == "normal"
    paths = lay_out(root, port, telemetry=telemetry)
    anchor = anchor_now()
    seeded = seed_metadata(paths.state_dir, anchor)
    create_accounts(paths, passwords, hashing or Passwords())
    collector = None
    if telemetry:
        collector = LiveCollector(paths.state_dir, seeded, anchor)
        collector.seed(anchor)
    return Install(paths, seeded, anchor, collector)
