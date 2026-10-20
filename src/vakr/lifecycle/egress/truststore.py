"""Build the TLS trust store: the OS CA bundle plus a directory of extra CAs.

A relocatable bundled Python interpreter is compiled with CA paths from the
machine it was built on, which may not exist on the production host, so this
module never uses ``load_default_certs`` or ``set_default_verify_paths``.
Instead it loads the distribution bundle explicitly (Ubuntu and RHEL family
paths) and then every PEM file in the operator's CA directory, which is
where an enterprise TLS-inspection CA is installed (EGRESS-002).

Dependencies:
    ssl: Client context with verification and hostname checking on.

Safety:
    The context requires certificate verification and hostname matching and
    refuses TLS older than 1.2. A malformed file in the CA directory is an
    error naming that file rather than being skipped, so a broken trust
    store is noticed instead of silently weakening or narrowing trust.

Example:
    >>> ctx = build_context(None, os_bundles=OS_BUNDLES)  # doctest: +SKIP
    >>> ctx.verify_mode == ssl.VerifyMode.CERT_REQUIRED  # doctest: +SKIP
    True
"""

import ssl
from collections.abc import Sequence
from pathlib import Path

from vakr.lifecycle.egress.errors import EgressError

OS_BUNDLES: tuple[Path, ...] = (
    Path("/etc/ssl/certs/ca-certificates.crt"),  # Debian/Ubuntu (ca-certificates)
    Path("/etc/pki/tls/certs/ca-bundle.crt"),  # RHEL, AlmaLinux, Rocky (ca-certificates)
)
"""Distribution CA bundles, checked in order; the first that exists is used."""

CA_SUFFIXES: tuple[str, ...] = (".pem", ".crt")
"""File suffixes loaded from the extra CA directory."""


def find_os_bundle(candidates: Sequence[Path] = OS_BUNDLES) -> Path | None:
    """Return the first distribution CA bundle that exists on this host.

    Args:
        candidates: Bundle paths in preference order.

    Returns:
        Path | None: The bundle to load, or ``None`` when none exists.

    Raises:
        None
    """
    return next((path for path in candidates if path.is_file()), None)


def ca_files(ca_dir: Path | None) -> list[Path]:
    """List the extra CA certificate files in ``ca_dir``, sorted by name.

    Args:
        ca_dir: Directory of operator-trusted CA certificates, or ``None``.

    Returns:
        list[Path]: Regular files with a ``.pem`` or ``.crt`` suffix; empty
        when the directory is ``None`` or does not exist.

    Raises:
        None
    """
    if ca_dir is None or not ca_dir.is_dir():
        return []
    return sorted(p for p in ca_dir.iterdir() if p.suffix in CA_SUFFIXES and p.is_file())


def build_context(ca_dir: Path | None, os_bundles: Sequence[Path] = OS_BUNDLES) -> ssl.SSLContext:
    """Create a verifying client context from the OS bundle and ``ca_dir``.

    Args:
        ca_dir: Extra trusted CAs (EGRESS-002), or ``None`` for none.
        os_bundles: Distribution bundle candidates; tests pass their own.

    Returns:
        ssl.SSLContext: Context with ``CERT_REQUIRED``, hostname checking,
        and a TLS 1.2 minimum.

    Raises:
        EgressError: Step ``"config"`` when a CA file cannot be loaded or no
            trust anchor exists at all.
    """
    # PROTOCOL_TLS_CLIENT turns on certificate and hostname verification.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    sources = [bundle] if (bundle := find_os_bundle(os_bundles)) else []
    sources += ca_files(ca_dir)
    if not sources:
        raise EgressError("config", "no trusted CA certificates found (OS bundle or CA directory)")
    for source in sources:
        try:
            context.load_verify_locations(cafile=source)
        except (ssl.SSLError, OSError) as exc:
            raise EgressError("config", f"cannot load CA file {source}: {exc}") from exc
    return context


def describe_sources(ca_dir: Path | None, os_bundles: Sequence[Path] = OS_BUNDLES) -> str:
    """Summarize where trust anchors come from, for diagnostics.

    Args:
        ca_dir: Extra CA directory, or ``None``.
        os_bundles: Distribution bundle candidates.

    Returns:
        str: For example ``"/etc/ssl/certs/ca-certificates.crt + 1 extra CA"``.

    Raises:
        None
    """
    bundle = find_os_bundle(os_bundles)
    extra = len(ca_files(ca_dir))
    base = str(bundle) if bundle else "no OS bundle"
    return f"{base} + {extra} extra CA{'s' if extra != 1 else ''}"
