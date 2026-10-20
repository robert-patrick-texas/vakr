"""Shared fixtures for configuration tests: a four-version schema and its migrations.

The packaged catalog is at ``config_version`` 1 with no migrations, so
upgrade behaviour (CFGM-002, CFGM-003, CFGM-T02, CFGM-T05) is exercised
against this fixture schema. Its release numbers (1.0.0 to 2.0.0) are made
up and deliberately unlike vakr's real version.

Dependencies:
    pytest: Fixtures.
    vakr.lifecycle.configmerge: The library under test.

Example:
    $ uv run pytest tests/unit/config
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from vakr.lifecycle.configmerge import (
    Catalog,
    ConfigLayout,
    Migration,
    parse_catalog,
    parse_migrations,
)

FIXTURE_CATALOG = """
[catalog]
config_version = 4

[[options]]
key = "update.channel"
type = "enum"
choices = ["stable", "prerelease"]
default = "stable"
since = "1.0.0"
reload = "live"
description = "Release channel."

[[options]]
key = "update.hold_version"
type = "str"
default = ""
since = "1.0.0"
reload = "live"
description = "Pinned release."

[[options]]
key = "poll.timeout_ms"
type = "int"
min = 1
default = 2000
since = "1.2.0"
reload = "restart"
description = "SNMP timeout in milliseconds."

[[options]]
key = "poll.interval"
type = "duration"
default = "60s"
since = "1.0.0"
reload = "restart"
description = "Polling interval."

[[options]]
key = "node.host"
type = "str"
default = ""
since = "1.3.0"
reload = "restart"
description = "Collector host name."

[[options]]
key = "node.port"
type = "int"
default = 8443
since = "1.3.0"
reload = "restart"
description = "Collector port."

[[options]]
key = "net.no_proxy"
type = "list[str]"
default = []
since = "1.0.0"
reload = "restart"
description = "Proxy bypass list."

[[options]]
key = "extra.flag"
type = "bool"
default = true
since = "2.0.0"
reload = "live"
description = "A feature added by the newest release."
"""

FIXTURE_MIGRATIONS = """
[[migrations]]
from = 1
to = 2
description = "Rename update.track and poll.wait; fold [upgrade] into [update]"
operations = [
    { op = "rename", from = "update.track", to = "update.channel" },
    { op = "map_values", key = "update.channel", map = { beta = "prerelease" } },
    { op = "move_table", from = "upgrade", to = "update" },
    { op = "rename", from = "poll.wait", to = "poll.timeout" },
]

[[migrations]]
from = 2
to = 3
description = "Timeouts in milliseconds; intervals as durations"
operations = [
    { op = "rename", from = "poll.timeout", to = "poll.timeout_ms" },
    { op = "scale", key = "poll.timeout_ms", factor = 1000 },
    { op = "convert", key = "poll.interval", to = "duration", unit = "s" },
]

[[migrations]]
from = 3
to = 4
description = "Split node.address; no_proxy becomes a list; drop [legacy]"
operations = [
    { op = "split", from = "node.address", into = ["node.host", "node.port"], separator = ":" },
    { op = "convert", key = "node.port", to = "int" },
    { op = "convert", key = "net.no_proxy", to = "list", separator = "," },
    { op = "remove", key = "legacy" },
]
"""

V1_CONFIG = """\
# Operator notes: production site
config_version = 1  # maintained by upgrade

[update]
# Follow pre-releases during the pilot
track = "beta"  # pilot channel

[upgrade]
hold_version = "1.0.3"  # pinned for change freeze

[poll]
wait = 2  # seconds; CPU-limited routers
interval = 60

[node]
address = "collector.example.net:8443"

[net]
no_proxy = "10.0.0.0/8, .corp.example"

[legacy]
option = true
"""


@pytest.fixture
def catalog4() -> Catalog:
    """Return the four-version fixture catalog.

    Args:
        None

    Returns:
        Catalog: Parsed ``FIXTURE_CATALOG``.

    Raises:
        None
    """
    return parse_catalog(FIXTURE_CATALOG, "fixture")


@pytest.fixture
def migrations4() -> tuple[Migration, ...]:
    """Return the fixture migration chain 1->2->3->4.

    Args:
        None

    Returns:
        tuple[Migration, ...]: Parsed ``FIXTURE_MIGRATIONS``.

    Raises:
        None
    """
    return parse_migrations(FIXTURE_MIGRATIONS, 4, "fixture")


@pytest.fixture
def layout(tmp_path: Path) -> ConfigLayout:
    """Return an operator-file layout under a temporary directory.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        ConfigLayout: ``<tmp>/etc`` with ``config.toml`` and ``conf.d``.

    Raises:
        None
    """
    (tmp_path / "etc/conf.d").mkdir(parents=True)
    return ConfigLayout(tmp_path / "etc")


@pytest.fixture
def write() -> Callable[[Path, str], Path]:
    """Return a helper that writes UTF-8 text and returns the path.

    Args:
        None

    Returns:
        Callable[[Path, str], Path]: ``write(path, text)``.

    Raises:
        None
    """

    def _write(path: Path, text: str) -> Path:
        """Write ``text`` to ``path``, creating parent directories.

        Args:
            path: Destination.
            text: Content.

        Returns:
            Path: ``path``.

        Raises:
            OSError: If the file cannot be written.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    return _write


@pytest.fixture
def v1_config() -> str:
    """Return an operator file written for config_version 1.

    Args:
        None

    Returns:
        str: ``V1_CONFIG``.

    Raises:
        None
    """
    return V1_CONFIG
