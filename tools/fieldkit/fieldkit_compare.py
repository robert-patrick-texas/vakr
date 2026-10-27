# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Preflight for the parallel-run profiles: ``mrtg``, ``smokeping``, and ``acceptance``.

The configuration of MRTG and SmokePing is read *before* collection
starts, for two reasons: an operator learns at once that a file is
missing or unreadable (MRTG files are often readable by root only, because
they hold SNMP communities), and every community and credential-like value
in them is known before the first bundle member is written, so the bundle
writer can redact them from every member (FIELD-004).

Dependencies:
    fieldkit_mrtgcfg, fieldkit_smokecfg: The parsers.

Safety:
    Error messages name files, never their content. Credentials shorter
    than ``MIN_SECRET`` characters are not redacted textually (that would
    rewrite ordinary words and numbers everywhere); they are kept out of the
    bundle by the parsers, which keep only allow-listed settings.

Example:
    >>> redactable({"public", "pw", "s3cr3t-community"})
    ['public', 's3cr3t-community']
"""

import argparse
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from fieldkit_mrtgcfg import parse_files as parse_mrtg
from fieldkit_mrtgmodel import DEFAULT_PATHS as MRTG_PATHS
from fieldkit_mrtgmodel import discover as discover_mrtg
from fieldkit_smokecfg import parse_files as parse_smokeping
from fieldkit_smokemodel import DEFAULT_PATHS as SMOKEPING_PATHS
from fieldkit_smokemodel import discover as discover_smokeping

COMPARE_PROFILES = ("mrtg", "smokeping")
MIN_SECRET = 4


class CompareError(Exception):
    """A parallel-run profile cannot start; nothing has been collected."""


def redactable(secrets: Iterable[str]) -> list[str]:
    """Return the credentials long enough to redact by search, sorted.

    Args:
        secrets: Communities, users, and secret values.

    Returns:
        list[str]: Those of at least ``MIN_SECRET`` characters.

    Raises:
        None
    """
    return sorted(s for s in set(secrets) if len(s) >= MIN_SECRET)


def compare_hosts(parsed: dict[str, Any]) -> list[str]:
    """List every host and slave name the parsed configuration mentions.

    Args:
        parsed: ``preflight_compare``'s configuration.

    Returns:
        list[str]: Names to register with the scrubber before any member is written.

    Raises:
        None
    """
    names: list[str] = []
    if "mrtg" in parsed:
        names += [h for t in parsed["mrtg"].targets for h in t.spec.hosts]
    if "smokeping" in parsed:
        names += [n for t in parsed["smokeping"].targets for n in (t.host, *t.slaves)]
    return list(dict.fromkeys(n for n in names if n))


def _readable(paths: list[Path], option: str, defaults: tuple[str, ...]) -> None:
    """Refuse a run whose configuration cannot be read.

    Args:
        paths: Files found.
        option: The option that names them.
        defaults: Where they are looked for by default.

    Returns:
        None

    Raises:
        CompareError: If none was found or the first cannot be read.
    """
    if not paths:
        raise CompareError(f"no configuration at {', '.join(defaults)}; pass {option} FILE")
    try:
        with paths[0].open("rb"):
            pass
    except OSError as exc:
        raise CompareError(f"cannot read {paths[0]} ({exc.strerror}); run with sudo") from None


def preflight_compare(
    args: argparse.Namespace, profiles: list[str]
) -> tuple[dict[str, Any], list[str]]:
    """Parse MRTG and SmokePing configuration for the profiles that need it.

    Args:
        args: Parsed options (``mrtg_config``, ``smokeping_config``,
            ``acceptance_targets``).
        profiles: Profiles of this run.

    Returns:
        tuple: ``{"mrtg": MrtgConfig, "smokeping": SmokeConfig}`` for the
        requested ones, and every credential found.

    Raises:
        CompareError: If configuration is missing or unreadable, or the
            acceptance targets file cannot be read.
    """
    parsed: dict[str, Any] = {}
    secrets: set[str] = set()
    if "mrtg" in profiles:
        paths = discover_mrtg(args.mrtg_config)
        _readable(paths, "--mrtg-config", tuple(args.mrtg_config) or MRTG_PATHS)
        parsed["mrtg"] = parse_mrtg(paths)
        secrets |= parsed["mrtg"].secrets
    if "smokeping" in profiles:
        paths = discover_smokeping(args.smokeping_config)
        _readable(paths, "--smokeping-config", tuple(args.smokeping_config) or SMOKEPING_PATHS)
        parsed["smokeping"] = parse_smokeping(paths)
        secrets |= parsed["smokeping"].secrets
    targets = args.acceptance_targets
    if "acceptance" in profiles and targets is not None and not targets.is_file():
        raise CompareError(f"--acceptance-targets {targets} is not a file")
    return parsed, sorted(secrets)
