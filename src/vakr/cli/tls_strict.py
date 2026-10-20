"""Operator messages for vakr's strict X.509 policy (EGRESS-003, EGRESS-007).

Python 3.13+ verifies TLS with OpenSSL's strict X.509 profile, which
rejects many enterprise TLS-inspection CAs even after they are trusted. The
owner's policy: ``vakr tls trust-ca`` refuses such a CA by default, lists
each missing item, and asks for a reissue; ``network.tls_strict_x509 =
false`` is the opt-in fallback that relaxes only the strict profile. This
module words those messages and reads the setting for ``trust-ca``.

Dependencies:
    vakr.config.loader: Reads the effective configuration.
    vakr.health.lifecycle_checks: Interprets ``network.tls_strict_x509``.

Safety:
    Messages name certificate files and missing extensions only. An
    unreadable configuration keeps the strict profile on.

Example:
    >>> "network.tls_strict_x509 = false" in refusal(Path("ca.pem"), ["keyUsage extension"])
    True
"""

from collections.abc import Sequence
from pathlib import Path

from vakr.config.loader import load_effective_config
from vakr.health.lifecycle_checks import STRICT_X509_OPTION, strict_x509_enabled

FALLBACK = f"{STRICT_X509_OPTION} = false"
STRICT_HINT = (
    "ask for certificates that meet the strict X.509 profile; if that is not possible, "
    f"set {FALLBACK} (EGRESS-007), which relaxes only those rules"
)
"""Diagnosis hint when only the strict profile rejects a chain (EGRESS-005)."""


def configured(config_dir: Path) -> bool:
    """Return whether ``trust-ca`` must enforce the strict X.509 profile.

    Args:
        config_dir: vakr configuration directory.

    Returns:
        bool: The effective ``network.tls_strict_x509``; ``True`` when the
        configuration cannot be read at all.

    Raises:
        None
    """
    try:
        return strict_x509_enabled(load_effective_config(config_dir, strict=False))
    except OSError, ValueError:
        return True


def refusal(source: Path, gaps: Sequence[str]) -> str:
    """Explain why a CA was refused under the strict profile (EGRESS-003).

    Args:
        source: File the operator offered.
        gaps: Missing items from ``certinfo.strict_profile_gaps``.

    Returns:
        str: Multi-line message listing every gap, the reissue request,
        and the fallback.

    Raises:
        None
    """
    lines = [
        f"refusing {source}: this CA does not meet the strict X.509 profile that "
        "Python 3.13+ enforces (VERIFY_X509_STRICT), so every handshake through it "
        "would fail. It lacks:",
        *(f"  - {gap}" for gap in gaps),
        "Ask the team that runs this CA to reissue it with these items.",
        f"If a reissue is not possible, set {FALLBACK} in the [network] table of the",
        "vakr configuration (EGRESS-007). That relaxes only the strict-profile rules;",
        "chain signatures, validity, purpose, and host names are still verified.",
        "Then run trust-ca again; nothing was installed",
    ]
    return "\n".join(lines)


def relaxed_warning(gaps: Sequence[str]) -> str:
    """Warn that a non-compliant CA is accepted only because of the fallback.

    Args:
        gaps: Missing items from ``certinfo.strict_profile_gaps``.

    Returns:
        str: Multi-line warning listing every gap.

    Raises:
        None
    """
    lines = [
        "WARNING: this CA does not meet the strict X.509 profile. It lacks:",
        *(f"  - {gap}" for gap in gaps),
        f"WARNING: it is accepted only because {FALLBACK} (EGRESS-007). Chain",
        "signatures, validity, purpose, and host names are still verified, and",
        "`vakr check health` reports WARNING while the profile is relaxed. Ask for a",
        f"reissued CA, then remove {STRICT_X509_OPTION} from the configuration again.",
    ]
    return "\n".join(lines)
