# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Receive field bundles on the development host: fetch, decrypt, verify, extract.

The other half of the field kit (FIELD-007, FIELD-008). Operators either put
the encrypted ``*.tar.gz.p7m`` at an HTTPS URL (``fetch URL``) or ``scp`` it
into the inbox directory. ``process`` then decrypts each bundle with the
recipient private key kept outside the repository, verifies every member
against ``manifest.json``, extracts it into the git-ignored ``inbox/<bundle-id>/``,
prints a summary, and moves the encrypted file to ``processed/``.
``init-recipient`` creates the key pair once per development host.

Dependencies:
    field_inbox_setup: Paths from ``fieldkit.toml`` and ``init-recipient``.
    fieldkit_crypto: ``openssl cms`` decryption.
    fieldkit_egress: Verifying TLS context for ``fetch`` (EGRESS-002).
    field_inbox_extract: Safe verified extraction.
    field_inbox_summary: Operator-readable summary.

Safety:
    The private key must be mode 0600 and is never written by this tool
    once it exists. Decryption writes into a private temporary directory,
    and failed (possibly unauthenticated) plaintext is deleted. ``fetch``
    accepts only HTTPS with certificate verification.

Example:
    $ uv run tools/fieldkit/field_inbox.py init-recipient
    $ uv run tools/fieldkit/field_inbox.py process
    field-inbox: vakr-field-east1-20261020T101500Z -> inbox/vakr-field-east1-20261020T101500Z
"""

import argparse
import re
import sys
import tempfile
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

from field_inbox_extract import UnsafeBundleError, extract_verified
from field_inbox_setup import TOOL, InboxConfig, init_recipient, load_config
from field_inbox_summary import summarize
from fieldkit_crypto import CryptoError, decrypt
from fieldkit_egress import tls_context

HERE = Path(__file__).resolve().parent
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.p7m$")
MAX_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024
CHUNK = 1024 * 1024


def fetch(url: str, cfg: InboxConfig, ca_file: Path | None, name: str | None) -> Path:
    """Download an encrypted bundle from an HTTPS URL into the inbox (FIELD-007).

    Args:
        url: ``https://`` URL of the ``*.p7m``.
        cfg: Resolved paths.
        ca_file: Extra trusted CA, or ``None``.
        name: File name to save as; defaults to the URL's last path segment.

    Returns:
        Path: The downloaded file.

    Raises:
        ValueError: If the URL is not HTTPS or the name is unsafe.
        OSError, urllib.error.URLError: If the download fails.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise ValueError("only https:// URLs are accepted")
    name = name or PurePosixPath(urllib.parse.unquote(parts.path)).name
    if not NAME_RE.match(name):
        raise ValueError(f"{name!r} is not a *.p7m file name; pass --name")
    cfg.inbox.mkdir(mode=0o700, parents=True, exist_ok=True)
    target, partial = cfg.inbox / name, cfg.inbox / f".{name}.partial"
    if target.exists():
        raise ValueError(f"{target} already exists")
    context, _ = tls_context(ca_file)
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))
    # The scheme was checked above, so urllib never sees file:// or custom schemes.
    request = urllib.request.Request(url, headers={"User-Agent": TOOL})  # noqa: S310
    total = 0
    with opener.open(request, timeout=60) as response, partial.open("xb") as out:
        while chunk := response.read(CHUNK):
            total += len(chunk)
            if total > MAX_BUNDLE_BYTES:
                out.close()
                partial.unlink()
                raise ValueError(f"download is larger than {MAX_BUNDLE_BYTES} bytes")
            out.write(chunk)
    partial.replace(target)
    return target


def process_one(path: Path, cfg: InboxConfig) -> Path:
    """Decrypt, verify, extract, summarize, and file away one bundle.

    Args:
        path: ``*.tar.gz.p7m`` (or an unencrypted ``*.tar.gz``).
        cfg: Resolved paths.

    Returns:
        Path: The extraction directory.

    Raises:
        CryptoError: If decryption or authentication fails.
        UnsafeBundleError: If the bundle is unsafe or fails verification.
        PermissionError: If the private key is accessible to others.
    """
    with tempfile.TemporaryDirectory(prefix="field-inbox-") as work:
        plain = path
        if path.name.endswith(".p7m"):
            if cfg.key.stat().st_mode & 0o077:
                raise PermissionError(f"{cfg.key} must be mode 0600")
            plain = Path(work) / "bundle.tar.gz"
            decrypt(cfg.openssl, cfg.recipient, cfg.key, path, plain)
        dest, manifest = extract_verified(plain, cfg.dest, MAX_BUNDLE_BYTES)
    print(summarize(dest, manifest))
    processed = cfg.inbox / "processed"
    processed.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.resolve() == cfg.inbox:
        path.replace(processed / path.name)
    return dest


def process(files: list[Path], cfg: InboxConfig) -> int:
    """Process the given bundles, or every bundle waiting in the inbox.

    Args:
        files: Explicit bundle paths; empty means all of ``cfg.inbox``.
        cfg: Resolved paths.

    Returns:
        int: 0 if every bundle succeeded (or none was waiting), else 1.

    Raises:
        None
    """
    pending = files or sorted([*cfg.inbox.glob("*.p7m"), *cfg.inbox.glob("*.tar.gz")])
    if not pending:
        print(f"{TOOL}: nothing to process in {cfg.inbox}")
    failures = 0
    for path in pending:
        try:
            process_one(path, cfg)
        except (CryptoError, UnsafeBundleError, OSError) as exc:
            failures += 1
            print(f"{TOOL}: {path.name}: {exc}; left in place", file=sys.stderr)
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser.

    Args:
        None

    Returns:
        argparse.ArgumentParser: Parser with ``init-recipient``, ``fetch``,
        and ``process`` commands.

    Raises:
        None
    """
    parser = argparse.ArgumentParser(prog=TOOL, description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=HERE / "fieldkit.toml")
    for option in ("--recipient", "--key", "--inbox-dir", "--dest"):
        parser.add_argument(option, type=Path)
    parser.add_argument("--openssl", default="openssl")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    commands.add_parser("init-recipient", help="create the recipient key pair and inbox")
    fetch_cmd = commands.add_parser("fetch", help="download a bundle from an HTTPS URL")
    fetch_cmd.add_argument("url")
    fetch_cmd.add_argument("--ca-file", type=Path)
    fetch_cmd.add_argument("--name", help="save under this *.p7m name")
    fetch_cmd.add_argument("--process", action="store_true", help="process it right away")
    process_cmd = commands.add_parser("process", help="decrypt, verify, and extract bundles")
    process_cmd.add_argument("files", nargs="*", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the inbox tool.

    Args:
        argv: Arguments without the program name.

    Returns:
        int: 0 on success, 1 on failure, 2 for configuration errors.

    Raises:
        SystemExit: From argparse for usage errors.
    """
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args)
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        print(f"{TOOL}: bad configuration {args.config}: {exc}", file=sys.stderr)
        return 2
    if args.command == "init-recipient":
        return init_recipient(cfg)
    if args.command == "process":
        return process(args.files, cfg)
    try:
        target = fetch(args.url, cfg, args.ca_file, args.name)
    except (ValueError, OSError) as exc:
        print(f"{TOOL}: fetch failed: {exc}", file=sys.stderr)
        return 1
    print(f"{TOOL}: saved {target}")
    return process([target], cfg) if args.process else 0


if __name__ == "__main__":
    sys.exit(main())
