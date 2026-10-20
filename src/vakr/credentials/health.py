"""Self-health check for the protected credentials file (SEC-016, OPS-001).

A credentials file with unsafe permissions is a security incident, and an
invalid one stops every SNMP poll at its next rotation, so both page through
CheckMK or Nagios as CRITICAL. A fresh install has no credentials yet; that
is a WARNING with a pointer to the documentation, not a failure. A host
without ``/etc/vakr/secrets`` is not an installed vakr node (the package's
tmpfiles.d entry always creates that directory), so there is nothing to
check and the result is OK.

Dependencies:
    vakr.credentials.provider: Strict-mode load of the file.
    vakr.health.model: Check result types.
    vakr.paths: Locates the secrets directory.

Safety:
    Reads local files only, always in strict mode. Summaries hold counts,
    paths, modes, and owners, never values.

Example:
    $ vakr check health
    VAKR WARNING - credentials: no credentials file yet ...
"""

from vakr.credentials.errors import (
    CredentialFileError,
    CredentialFileMissingError,
    CredentialFileUnreadableError,
    CredentialPermissionError,
)
from vakr.credentials.provider import FileCredentialProvider
from vakr.health.model import CheckResult, PerfDatum, Status
from vakr.paths import InstallPaths

NAME = "credentials"
DOC_HINT = "see docs/operations/credentials.md"
CHECK_HINT = "run `vakr credentials check`"


def check_credentials() -> CheckResult:
    """Check that the credentials file is present, safe, and valid.

    Args:
        None

    Returns:
        CheckResult: OK when it passes strict checks (or the host is not an
        installed node), WARNING when it does not exist yet, UNKNOWN when it
        cannot be read by this user, CRITICAL when it is unsafe or invalid.

    Raises:
        None
    """
    secrets_dir = InstallPaths.from_environment().secrets_dir
    if not secrets_dir.exists(follow_symlinks=False):
        return CheckResult(NAME, Status.OK, f"no {secrets_dir}; not an installed vakr node")
    provider = FileCredentialProvider(secrets_dir)
    try:
        entries = provider.summaries()
    except CredentialFileMissingError:
        summary = f"no credentials file yet at {provider.path} (fresh install); {DOC_HINT}"
        return CheckResult(NAME, Status.WARNING, summary)
    except CredentialFileUnreadableError as exc:
        summary = f"cannot read {provider.path}: {exc.reason}; run as root or a vakr member"
        return CheckResult(NAME, Status.UNKNOWN, summary)
    except CredentialPermissionError as exc:
        more = f" (+{len(exc.problems) - 1} more)" if len(exc.problems) > 1 else ""
        summary = f"unsafe {provider.path}: {exc.problems[0]}{more}; {CHECK_HINT}"
        return CheckResult(NAME, Status.CRITICAL, summary)
    except CredentialFileError as exc:
        summary = f"invalid {provider.path}: {len(exc.issues)} problem(s); {CHECK_HINT}"
        return CheckResult(NAME, Status.CRITICAL, summary)
    finally:
        provider.forget()
    snmp = sum(1 for e in entries if e.kind == "snmp")
    named = len(entries) - snmp
    summary = f"{snmp} SNMP credential(s), {named} named secret(s); permissions safe"
    return CheckResult(NAME, Status.OK, summary, (PerfDatum("credentials", float(snmp)),))
