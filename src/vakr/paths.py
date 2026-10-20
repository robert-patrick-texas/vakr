"""Filesystem locations used by an installed vakr node.

The ``.deb`` installs everything under ``/opt/vakr`` (PKG-002), while
operator configuration, state, cache, and runtime files live in their usual
system locations so backups, permissions, and package upgrades treat them
correctly. Every component asks this module for paths instead of spelling
them out, which keeps the layout in one place and lets tests relocate a
whole installation under a temporary directory.

Dependencies:
    os, pathlib: Environment override and path construction.

Safety:
    Setting ``VAKR_ROOT`` relocates every path under that prefix. It exists
    for tests and container builds; production units never set it.

Example:
    >>> paths = InstallPaths.from_environment({"VAKR_ROOT": "/srv/t"})
    >>> str(paths.config_dir)
    '/srv/t/etc/vakr'
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class InstallPaths:
    """The directory layout of one vakr installation.

    Instances are immutable. All attributes are absolute paths.

    Attributes:
        root: Prefix applied to every path (``/`` in production).
        opt_dir: Package payload: interpreter, venv, defaults, signers.
        config_dir: Operator configuration (overrides only, CFGM-001).
        secrets_dir: Protected credentials (mode 0750, root:vakr).
        ca_dir: Extra trusted CA certificates for outbound HTTPS (EGRESS-002).
        state_dir: Durable state: databases, spool, update records.
        cache_dir: Downloaded and previous packages for rollback.
        run_dir: Runtime sockets and locks (tmpfs).
    """

    root: Path
    opt_dir: Path
    config_dir: Path
    secrets_dir: Path
    ca_dir: Path
    state_dir: Path
    cache_dir: Path
    run_dir: Path

    @classmethod
    def under(cls, root: Path) -> InstallPaths:
        """Build the standard layout beneath ``root``.

        Args:
            root: Installation prefix; ``Path("/")`` for a real host.

        Returns:
            InstallPaths: Paths for that prefix.

        Raises:
            None
        """
        config = root / "etc/vakr"
        return cls(
            root=root,
            opt_dir=root / "opt/vakr",
            config_dir=config,
            secrets_dir=config / "secrets",
            ca_dir=config / "tls/ca.d",
            state_dir=root / "var/lib/vakr",
            cache_dir=root / "var/cache/vakr",
            run_dir=root / "run/vakr",
        )

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> InstallPaths:
        """Return the layout for this process, honouring ``VAKR_ROOT``.

        Args:
            environ: Environment to read; ``None`` means ``os.environ``.

        Returns:
            InstallPaths: Production layout, or one relocated under
            ``VAKR_ROOT`` when that variable is set and non-empty.

        Raises:
            None
        """
        env = os.environ if environ is None else environ
        return cls.under(Path(env.get("VAKR_ROOT") or "/"))

    @property
    def defaults_dir(self) -> Path:
        """Packaged, documented defaults for every option (read-only).

        Args:
            None

        Returns:
            Path: ``/opt/vakr/share/defaults``.

        Raises:
            None
        """
        return self.opt_dir / "share/defaults"

    @property
    def release_signers(self) -> Path:
        """Trusted release signing keys in ``ssh-keygen`` allowed_signers format.

        Args:
            None

        Returns:
            Path: ``/opt/vakr/share/release-signers``.

        Raises:
            None
        """
        return self.opt_dir / "share/release-signers"
