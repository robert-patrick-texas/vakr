"""Inventory settings from configuration, and the ``vakr config validate`` rule (CONF-009).

``inventory.file`` locates the file (empty: ``inventory.toml`` in the
configuration directory, 11.1). ``security.allowed_target_cidrs`` and
``security.denied_target_addresses`` are the target guardrails (15.5).
Credential *names* come from the protected provider; a provider that
cannot be read is reported, never guessed around.

``inventory_problems`` is the cross-option rule ``vakr config validate``
runs: when the inventory file exists, it must parse and pass every check
that does not need the metadata database. ``vakr inventory validate`` also
checks the file against storage (CONF-008). The rule looks for the file in
the installed configuration directory (relocated by ``VAKR_ROOT``), or at
``inventory.file`` when that is set.

Dependencies:
    vakr.credentials: The provider, for names only.
    vakr.inventory.files, guardrails, validate: Reading and checks.

Safety:
    Reads credential names, never secret values; messages from the
    provider never contain secrets.

Example:
    >>> from vakr.config.loader import EffectiveConfig
    >>> target_policy(EffectiveConfig({})).denied[0].with_prefixlen
    '169.254.169.254/32'
"""

from pathlib import Path

from vakr.config.loader import EffectiveConfig
from vakr.credentials.errors import CredentialError
from vakr.credentials.provider import FileCredentialProvider
from vakr.inventory.errors import InventoryError, render_issues
from vakr.inventory.files import inventory_path, read_inventory
from vakr.inventory.guardrails import ALLOWED_KEY, DEFAULT_DENIED, DENIED_KEY, TargetPolicy
from vakr.inventory.snapshot import EMPTY
from vakr.inventory.validate import Checks, validate
from vakr.paths import InstallPaths

FILE_KEY = "inventory.file"


def target_policy(config: EffectiveConfig) -> TargetPolicy:
    """Build the target guardrails from configuration.

    Args:
        config: The effective configuration.

    Returns:
        TargetPolicy: Allow and deny networks.

    Raises:
        ValueError: If an entry is not an address or CIDR.
    """
    allowed = config.get(ALLOWED_KEY) or []
    denied = config.get(DENIED_KEY, list(DEFAULT_DENIED))
    return TargetPolicy.parse(list(allowed), list(denied or []))


def credential_names(
    secrets_dir: Path | None, *, development: bool = False
) -> tuple[frozenset[str] | None, str | None]:
    """Return the SNMP credential names the provider defines.

    Args:
        secrets_dir: Directory of ``credentials.toml``; ``None`` for the installed one.
        development: Tolerate unsafe permissions (developer workstations only).

    Returns:
        tuple[frozenset[str] | None, str | None]: The names, or ``None``
        and why the provider could not be read.

    Raises:
        None
    """
    try:
        found = FileCredentialProvider(secrets_dir, development=development).summaries()
    except CredentialError as exc:
        return None, str(exc)
    return frozenset(s.name for s in found if s.kind == "snmp"), None


def checks_for(config: EffectiveConfig, secrets_dir: Path | None, *, development: bool) -> Checks:
    """Build validation checks from configuration and the credential provider.

    Args:
        config: The effective configuration.
        secrets_dir: Directory of ``credentials.toml``.
        development: Tolerate unsafe credential-file permissions.

    Returns:
        Checks: Credential names and guardrails.

    Raises:
        ValueError: If a guardrail entry is invalid.
    """
    names, why = credential_names(secrets_dir, development=development)
    return Checks(names, why, target_policy(config))


def configured_path(config: EffectiveConfig, config_dir: Path) -> Path:
    """Return where the inventory file is.

    Args:
        config: The effective configuration.
        config_dir: The configuration directory.

    Returns:
        Path: ``inventory.file``, or ``inventory.toml`` in ``config_dir``.

    Raises:
        None
    """
    return inventory_path(config_dir, config.get(FILE_KEY) or "")


def inventory_problems(config: EffectiveConfig) -> list[str]:
    """``vakr config validate`` rule: the inventory file, if any, is valid.

    Args:
        config: The effective configuration.

    Returns:
        list[str]: One ``file: path: reason`` line per problem.

    Raises:
        None
    """
    paths = InstallPaths.from_environment()
    try:
        inv = read_inventory(configured_path(config, paths.config_dir))
        if inv is None:
            return []
        checks = checks_for(config, paths.secrets_dir, development=False)
    except InventoryError as exc:
        return exc.lines
    except ValueError as exc:
        return [str(exc)]
    return render_issues(inv.source, validate(inv, EMPTY, checks).problems)
