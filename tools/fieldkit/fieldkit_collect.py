# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Run one ``collect``: preflight, profiles, bundle, encryption, final line.

Everything that can be checked before collection starts is checked first
(profiles, device list, community file permissions, OpenSSL version,
recipient certificate, output directory), so an operator never waits ten
minutes for a walk only to learn the bundle cannot be encrypted. Profiles
then run in a fixed order under one time budget, and the run ends with
exactly one stdout line naming the output file and the next step (FIELD-009).

Dependencies:
    fieldkit_plan: Preflight validation of every input.
    fieldkit_bundle, fieldkit_context: Bundle writer and run context.
    fieldkit_crypto: CMS encryption.
    fieldkit_host, fieldkit_snmp, fieldkit_paths, fieldkit_app: Profiles.

Safety:
    The plaintext archive is deleted after successful encryption unless
    ``--keep-plaintext`` is given; if encryption fails it is kept (it never
    left this host) and the final line says so. The community lives only in
    memory and in the private net-snmp directory, and is passed to the
    bundle writer as a string that must never appear in any member.

Example:
    >>> final_line(BuildInfo(), Path("b.tar.gz.p7m"), 0, True).startswith("fieldkit: wrote b.tar")
    True
"""

import argparse
import hashlib
import platform
import socket
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fieldkit_app import collect_app
from fieldkit_bundle import FORMAT_VERSION, BundleError, BundleWriter, safe_label, utc_stamp
from fieldkit_context import Context, make_scrub_hook
from fieldkit_crypto import CryptoError, encrypt
from fieldkit_host import collect_host
from fieldkit_paths import collect_paths, ping_target
from fieldkit_plan import Plan, PreflightError, make_plan
from fieldkit_pseudo import Pseudonymizer
from fieldkit_run import BuildInfo, StepLog, progress
from fieldkit_scrub import Scrubber
from fieldkit_snmp import collect_snmp
from fieldkit_snmpconf import CredentialError
from fieldkit_tunnel import redact_url

_PROFILES: dict[str, Callable[[Context], None]] = {
    "host": collect_host,
    "snmp": collect_snmp,
    "paths": collect_paths,
}


def _scrubber(args: argparse.Namespace, info: BuildInfo, plan: Plan, host: str) -> Scrubber | None:
    """Create the run's scrubber and teach it every host name we know.

    Args:
        args: Parsed options.
        info: Build information (default keywords).
        plan: Validated inputs.
        host: Local host name.

    Returns:
        Scrubber | None: ``None`` without ``--pseudonymize``.

    Raises:
        None
    """
    if plan.key is None:
        return None
    scrubber = Scrubber(Pseudonymizer(plan.key, [*info.keep_keywords, *args.keep_keyword]))
    names = [host, host.split(".")[0], args.peer or ""]
    names += [n for entry in plan.devices for n in (entry, ping_target(entry))]
    if args.proxy:
        names.append(redact_url(args.proxy).rsplit("@", 1)[-1].split("//")[-1].split(":")[0])
    for name in names:
        scrubber.add_host(name)
    return scrubber


def _parameters(args: argparse.Namespace, plan: Plan) -> dict[str, Any]:
    """List the run's non-secret parameters for the manifest (FIELD-003).

    Args:
        args: Parsed options.
        plan: Validated inputs.

    Returns:
        dict[str, Any]: Parameters; the community itself is never included,
        only where it came from, and proxy credentials are redacted.

    Raises:
        None
    """
    source = ("file" if args.community_file else "prompt") if plan.community else None
    return {
        "devices_file": args.devices.name if args.devices else None,
        "device_count": len(plan.devices),
        "peer": args.peer,
        "extra_oids": args.extra_oid,
        "community_source": source,
        "proxy": redact_url(args.proxy) if args.proxy else None,
        "ca_file": str(args.ca_file) if args.ca_file else None,
        "strict_x509": not args.no_strict_x509,
        "egress_test": not args.no_egress,
        "limits": {k: getattr(args, k) for k in ("time_budget", "max_bytes", "max_member_bytes")},
        "snmp": {k: getattr(args, k) for k in ("walk_timeout", "device_pause", "snmp_timeout")},
    }


def _manifest(ctx: Context, plan: Plan, bundle_id: str, when: datetime) -> dict[str, Any]:
    """Assemble the manifest fields other than members and notes (FIELD-003).

    Args:
        ctx: Finished run context.
        plan: Validated inputs.
        bundle_id: Bundle identifier (file name without extension).
        when: Run start time.

    Returns:
        dict[str, Any]: Non-secret description of the run.

    Raises:
        None
    """
    args, info, scrubber = ctx.args, ctx.info, ctx.scrubber
    key_id = hashlib.sha256(b"fieldkit key id\0" + plan.key).hexdigest()[:16] if plan.key else None
    return {
        "format": FORMAT_VERSION,
        "bundle_id": bundle_id,
        "created_utc": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": {
            "name": info.tool_name,
            "version": info.version,
            "python": platform.python_version(),
        },
        "profiles": plan.profiles,
        "parameters": _parameters(args, plan),
        "encryption": {
            "enabled": not args.no_encrypt,
            "openssl": plan.openssl,
            "recipient": plan.recipient_info,
        },
        "pseudonymization": {
            "enabled": scrubber is not None,
            "key_id": key_id,
            "keep_keywords": sorted({*info.keep_keywords, *args.keep_keyword}) if scrubber else [],
            "ipv4_collisions": scrubber.p.collisions if scrubber else 0,
        },
        "steps": ctx.steps.steps,
        "errors": ctx.steps.errors(),
    }


def _collect(args: argparse.Namespace, info: BuildInfo, plan: Plan) -> tuple[Path, int]:
    """Run every profile and write the plaintext bundle.

    Args:
        args: Parsed options.
        info: Build information.
        plan: Validated inputs.

    Returns:
        tuple[Path, int]: The ``.tar.gz`` and the number of failed steps.

    Raises:
        OSError, BundleError: If the bundle cannot be written.
        KeyboardInterrupt: Re-raised after the partial file is removed.
    """
    when = datetime.now(UTC).replace(microsecond=0)
    host = socket.gethostname()
    scrubber = _scrubber(args, info, plan, host)
    host_label = safe_label(scrubber.p.hostname(host) if scrubber else host.split(".")[0])
    bundle_id = f"{info.bundle_prefix}-{host_label}-{utc_stamp(when)}"
    secrets = [plan.community.encode()] if plan.community else []
    limits = (args.max_bytes, args.max_member_bytes)
    target = args.output_dir / f"{bundle_id}.tar.gz"
    if target.exists() or target.with_name(target.name + ".p7m").exists():
        raise FileExistsError(f"{target} already exists; rerun in a second")
    writer = BundleWriter(target, when, limits, secrets, make_scrub_hook(scrubber))
    steps = StepLog(deadline=time.monotonic() + args.time_budget)
    ctx = Context(args, info, writer, steps, scrubber, plan.devices, plan.community)
    profiles = {**_PROFILES, **({info.app_name: collect_app} if info.app_name else {})}
    try:
        for name in plan.profiles:
            progress(info.tool_name, f"collecting profile {name}")
            with steps.step(f"profile.{name}"):
                profiles[name](ctx)
        path = writer.close(_manifest(ctx, plan, bundle_id, when))
    except BaseException:
        writer.abort()
        raise
    return path, len(steps.errors())


def final_line(info: BuildInfo, path: Path, errors: int, encrypted: bool) -> str:
    """Compose the one line that ends every successful run (FIELD-009).

    Args:
        info: Build information (tool name, inbox hint).
        path: Output file.
        errors: Number of failed steps.
        encrypted: Whether ``path`` is the encrypted bundle.

    Returns:
        str: ``<tool>: wrote <file>...; next: <step>``.

    Raises:
        None
    """
    note = f" ({errors} step errors are listed in manifest.json)" if errors else ""
    if encrypted:
        nxt = f"scp it to {info.inbox_hint} or put it at an HTTPS URL and send the link"
        return f"{info.tool_name}: wrote {path}{note}; next: {nxt}"
    nxt = "keep it on this host; rerun without --no-encrypt to send it to development"
    return f"{info.tool_name}: wrote {path} (NOT encrypted){note}; next: {nxt}"


def run_collect(args: argparse.Namespace, info: BuildInfo) -> int:
    """Handle ``collect``.

    Args:
        args: Parsed options.
        info: Build information.

    Returns:
        int: 0 when a bundle was written, 1 on collection or encryption
        failure, 2 on preflight failure, 130 when interrupted.

    Raises:
        None
    """
    tool = info.tool_name
    with tempfile.TemporaryDirectory(prefix="fieldkit-") as work:
        try:
            plan = make_plan(args, info, Path(work))
        except (PreflightError, CredentialError, CryptoError, ValueError, OSError) as exc:
            print(f"{tool}: cannot start: {exc}; no bundle was written")
            return 2
        try:
            path, errors = _collect(args, info, plan)
        except KeyboardInterrupt:
            print(f"{tool}: interrupted; the partial bundle was removed")
            return 130
        except (OSError, BundleError) as exc:
            print(f"{tool}: failed: {exc}; no bundle was written")
            return 1
        if args.no_encrypt or plan.recipient is None:
            print(final_line(info, path, errors, encrypted=False))
            return 0
        encrypted = path.with_name(path.name + ".p7m")
        try:
            encrypt(args.openssl, plan.recipient, path, encrypted)
        except CryptoError as exc:
            print(f"{tool}: {exc}; the unencrypted bundle stays at {path}", file=sys.stdout)
            return 1
    if not args.keep_plaintext:
        path.unlink(missing_ok=True)
    print(final_line(info, encrypted, errors, encrypted=True))
    return 0
