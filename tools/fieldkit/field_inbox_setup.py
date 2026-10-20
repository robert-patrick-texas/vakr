# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Development-host configuration and recipient key setup for the field inbox.

Field bundles are encrypted to one recipient certificate that ships inside
the field kit (FIELD-006); the matching private key lives only on the
development host, outside the repository (FIELD-008). ``init-recipient``
creates that RSA-3072 key once, a self-signed certificate for it, and the
``~/vakr-inbox/`` directory operators copy bundles into. Paths come from the
``[inbox]`` table of ``fieldkit.toml`` (section 29.1, rule 2).

Dependencies:
    tomllib: Reads ``fieldkit.toml``.
    fieldkit_crypto, fieldkit_run: OpenSSL calls with argument lists.

Safety:
    An existing private key is never overwritten. A new key is created with
    ``O_EXCL`` at mode 0600 in a 0700 directory; the key material passes
    through memory only, never through a temporary file.

Example:
    $ uv run tools/fieldkit/field_inbox.py init-recipient
    field-inbox: recipient CN=vakr field recipient SHA-256 16:44:...
"""

import argparse
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from fieldkit_crypto import certificate_info
from fieldkit_run import run_command

TOOL = "field-inbox"


@dataclass(frozen=True, slots=True)
class InboxConfig:
    """Resolved development-host paths.

    Attributes:
        recipient: Recipient certificate (public, committed).
        key: Recipient private key (outside the repository, 0600).
        inbox: Directory operators copy bundles into.
        dest: Git-ignored extraction root.
        cn: Common name for a new recipient certificate.
        days: Validity of a new recipient certificate.
        openssl: OpenSSL program.
    """

    recipient: Path
    key: Path
    inbox: Path
    dest: Path
    cn: str
    days: int
    openssl: str


def load_config(args: argparse.Namespace) -> InboxConfig:
    """Resolve paths from ``fieldkit.toml`` and command-line overrides.

    Args:
        args: Parsed options (``config``, ``recipient``, ``key``, ``inbox_dir``,
            ``dest``, ``openssl``).

    Returns:
        InboxConfig: Absolute paths.

    Raises:
        OSError, tomllib.TOMLDecodeError, KeyError: If the config is unusable.
    """
    with args.config.open("rb") as handle:
        config = tomllib.load(handle)
    base, kit, box = args.config.resolve().parent, config["fieldkit"], config["inbox"]

    def path(override: Path | None, value: str) -> Path:
        """Pick the override or resolve a config path.

        Args:
            override: Command-line value, if any.
            value: Path from the config file.

        Returns:
            Path: Absolute path.

        Raises:
            None
        """
        return (override or base / Path(value).expanduser()).resolve()

    return InboxConfig(
        recipient=path(args.recipient, kit["recipient"]),
        key=path(args.key, box["recipient_key"]),
        inbox=path(args.inbox_dir, box["inbox_dir"]),
        dest=path(args.dest, box["extract_dir"]),
        cn=box.get("recipient_cn", "field recipient"),
        days=int(box.get("recipient_days", 3650)),
        openssl=args.openssl,
    )


def init_recipient(cfg: InboxConfig) -> int:
    """Create the recipient key (never overwritten), certificate, and inbox.

    Args:
        cfg: Resolved paths.

    Returns:
        int: 0 on success, 1 if OpenSSL fails.

    Raises:
        OSError: If a directory or file cannot be created.
    """
    cfg.key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    cfg.key.parent.chmod(0o700)
    if cfg.key.exists():
        print(f"{TOOL}: keeping existing key {cfg.key}")
    else:
        gen = [cfg.openssl, "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072"]
        result = run_command(gen, timeout=300)
        if not result.ok:
            print(f"{TOOL}: key generation failed: {result.error or result.stderr.strip()}")
            return 1
        _write_private(cfg.key, result.stdout)
    if not cfg.recipient.exists():
        cfg.recipient.parent.mkdir(parents=True, exist_ok=True)
        req = [cfg.openssl, "req", "-x509", "-new", "-key", str(cfg.key), "-subj", f"/CN={cfg.cn}"]
        req += ["-days", str(cfg.days), "-sha256", "-out", str(cfg.recipient)]
        req += ["-addext", "basicConstraints=critical,CA:FALSE"]
        req += ["-addext", "keyUsage=critical,keyEncipherment"]
        result = run_command(req, timeout=60)
        if not result.ok:
            print(f"{TOOL}: certificate creation failed: {result.stderr.strip()}")
            return 1
    for directory in (cfg.inbox, cfg.inbox / "processed"):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = certificate_info(cfg.openssl, cfg.recipient)
    print(f"{TOOL}: recipient {info.get('subject')} SHA-256 {info.get('sha256')}")
    print(f"{TOOL}: commit {cfg.recipient}; keep {cfg.key} private and backed up")
    return 0


def _write_private(path: Path, text: str) -> None:
    """Create a new file readable only by its owner.

    Args:
        path: File that must not exist yet.
        text: Content.

    Returns:
        None

    Raises:
        FileExistsError: If ``path`` already exists.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(text)
