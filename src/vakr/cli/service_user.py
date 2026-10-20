"""Run root-invoked maintenance commands as the ``vakr`` service user.

Operators naturally type ``sudo vakr db migrate``. If that ran as root, any
database, WAL, or shared-memory file it created would be root-owned, and the
``vakr`` services could no longer write their own data. Commands that touch
``/var/lib/vakr`` therefore switch to the service user before opening
anything, so files keep the ownership the package set up (PKG-003).

Dependencies:
    os, pwd: Look up the service account and drop privileges.

Safety:
    Only ever lowers privileges. Without root, outside an installed node
    (``VAKR_ROOT`` set for tests), or on a host with no ``vakr`` account, it
    changes nothing.

Example:
    >>> drop_to_service_user(euid=1000) is None
    True
"""

import os
import pwd

SERVICE_USER = "vakr"


def drop_to_service_user(user: str = SERVICE_USER, *, euid: int | None = None) -> str | None:
    """Switch the process to ``user`` when it runs as root on an installed node.

    Args:
        user: Service account to become.
        euid: Effective UID to assume, for tests; ``None`` reads the real one.

    Returns:
        str | None: ``None`` when nothing needed changing or the switch
        worked; otherwise an operator-readable reason it failed.

    Raises:
        None
    """
    if (os.geteuid() if euid is None else euid) != 0 or os.environ.get("VAKR_ROOT"):
        return None
    try:
        account = pwd.getpwnam(user)
    except KeyError:
        return None  # a development host without the package's service account
    try:
        # Groups first, then GID, then UID: after setuid the process can no
        # longer change its groups.
        os.initgroups(user, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    except OSError as exc:
        return f"cannot switch to the {user} service user: {exc.strerror}"
    os.environ["HOME"] = account.pw_dir
    return None
