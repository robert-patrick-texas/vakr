# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Preflight for ``collect``: validate every input before collection starts.

An operator should never wait through ten minutes of SNMP walks only to
learn that the bundle cannot be encrypted. Everything checkable up front is
checked here, in this order: profile names, the device list, the peer, extra
OIDs, the output directory, OpenSSL 3.0+ and the recipient certificate
(FIELD-006), then the SNMP community (prompting last, so nothing else can
fail after the operator typed it), then the pseudonym key (FIELD-005).

Dependencies:
    fieldkit_crypto: OpenSSL preflight, certificate details, pseudonym key.
    fieldkit_snmpconf: Community, device-list, host, and OID validation.

Safety:
    Error messages name files and options, never the community. The only
    file written here is the pseudonym key, and only with ``--pseudonymize``.

Example:
    >>> ordered_profiles("paths,host", BuildInfo())
    ['host', 'paths']
"""

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from fieldkit_crypto import certificate_info, load_or_create_key, preflight
from fieldkit_run import BuildInfo
from fieldkit_snmpconf import check_host, check_oid, parse_devices, read_community

BASE_PROFILES = ("host", "snmp", "paths")
"""Built-in profiles in run order; the application profile runs last."""


class PreflightError(Exception):
    """The run cannot start; nothing has been collected."""


@dataclass
class Plan:
    """Validated inputs for one run.

    Attributes:
        profiles: Profiles to run, in canonical order.
        devices: Validated device entries.
        recipient: Recipient certificate path, or ``None`` with ``--no-encrypt``.
        recipient_info: Subject, expiry, and fingerprint of the recipient.
        openssl: ``openssl version`` line, or ``""`` with ``--no-encrypt``.
        community: SNMPv2c community, or ``""``; hidden from ``repr``.
        key: Pseudonym key, or ``None``; hidden from ``repr``.
    """

    profiles: list[str]
    devices: list[str] = field(default_factory=list)
    recipient: Path | None = None
    recipient_info: dict[str, str] = field(default_factory=dict)
    openssl: str = ""
    community: str = field(default="", repr=False)
    key: bytes | None = field(default=None, repr=False)


def profile_names(info: BuildInfo) -> list[str]:
    """List every profile name in run order.

    Args:
        info: Build information; adds the application profile if named.

    Returns:
        list[str]: ``host``, ``snmp``, ``paths``, then the application.

    Raises:
        None
    """
    return [*BASE_PROFILES, *([info.app_name] if info.app_name else [])]


def ordered_profiles(text: str, info: BuildInfo) -> list[str]:
    """Parse ``--profile`` into known profile names in canonical order.

    Args:
        text: Comma-separated profile names.
        info: Build information.

    Returns:
        list[str]: Requested profiles in run order.

    Raises:
        PreflightError: If a name is unknown or the list is empty.
    """
    wanted = {name.strip() for name in text.split(",") if name.strip()}
    known = profile_names(info)
    unknown = sorted(wanted - set(known))
    if unknown or not wanted:
        raise PreflightError(f"unknown profile {', '.join(unknown) or '(none)'}; use {known}")
    return [name for name in known if name in wanted]


def _recipient(args: argparse.Namespace, info: BuildInfo, work: Path) -> Path:
    """Return the recipient certificate: ``--recipient`` or the built-in one.

    Args:
        args: Parsed options.
        info: Build information carrying the embedded certificate.
        work: Private temporary directory for the embedded copy.

    Returns:
        Path: PEM certificate file.

    Raises:
        PreflightError: If no certificate is available.
    """
    if args.recipient is not None:
        return args.recipient
    if not info.recipient_pem:
        raise PreflightError("no built-in recipient certificate; pass --recipient CERT")
    path = work / "recipient.pem"
    path.write_text(info.recipient_pem, encoding="ascii")
    return path


def make_plan(args: argparse.Namespace, info: BuildInfo, work: Path) -> Plan:
    """Validate every input before any collection starts.

    Args:
        args: Parsed options; ``extra_oid`` is normalized in place.
        info: Build information.
        work: Private temporary directory.

    Returns:
        Plan: Validated inputs.

    Raises:
        PreflightError, CredentialError, CryptoError, ValueError, OSError:
            On the first problem found; messages never contain the community.
    """
    plan = Plan(ordered_profiles(args.profile, info))
    if args.devices is not None:
        text = args.devices.read_text(encoding="utf-8")
        plan.devices = parse_devices(text, args.devices.name, args.max_devices)
    if "snmp" in plan.profiles and not plan.devices:
        raise PreflightError("--profile snmp needs --devices FILE")
    if "paths" in plan.profiles and not (args.peer or plan.devices):
        raise PreflightError("--profile paths needs --peer HOST or --devices FILE")
    if args.peer:
        check_host(args.peer, "--peer")
    args.extra_oid = [check_oid(oid) for oid in args.extra_oid]
    if not args.output_dir.is_dir():
        raise PreflightError(f"--output-dir {args.output_dir} is not a directory")
    if not args.no_encrypt:
        plan.openssl = preflight(args.openssl)
        plan.recipient = _recipient(args, info, work)
        plan.recipient_info = certificate_info(args.openssl, plan.recipient)
    if "snmp" in plan.profiles:
        plan.community = read_community(args.community_file)
    if args.pseudonymize:
        plan.key = load_or_create_key(Path.home() / ".config" / info.tool_name / "pseudonym.key")
    return plan
