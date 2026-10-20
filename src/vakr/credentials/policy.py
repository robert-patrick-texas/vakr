"""Ownership and permission policy for the credentials file (SEC-016, CRED-001).

Anyone who can write ``credentials.toml`` can redirect vakr's SNMP
credentials, and anyone who can read it can poll every device. The file must
therefore be a regular file, owned by root or the ``vakr`` service user, not
writable by group or others, not readable by others, and readable by group
only when that group is ``vakr``; the expected setup is mode ``0640``,
``root:vakr``. Its directory must not be group- or world-writable, or anyone
in that group could replace the file.

These are pure rules over ``stat`` results; ``vakr.credentials.protected``
applies them to the open file descriptor. Strict mode, the production
default, refuses a file that breaks any rule. Development mode, an explicit
flag and never an environment variable, turns the problems into warnings.

Dependencies:
    os, stat: ``stat`` results and mode bits.
    pwd, grp: Look up the ``vakr`` user and group.

Safety:
    Problem phrases contain modes, uids, and gids only, never file content.

Example:
    >>> import os, stat
    >>> st = os.stat_result((stat.S_IFREG | 0o644, 0, 0, 1, 0, 0, 0, 0, 0, 0))
    >>> file_problems(st, FilePolicy(frozenset({0}), None))
    ['file is readable by others (mode 0644); expected 0640']
"""

import contextlib
import grp
import os
import pwd
import stat
from dataclasses import dataclass
from pathlib import Path

SERVICE_USER = "vakr"
SERVICE_GROUP = "vakr"

type Signature = tuple[int, ...]
"""Directory and file identity and change times; any change means reload."""


@dataclass(frozen=True, slots=True)
class FilePolicy:
    """Who may own the credentials file and which group may read it.

    Attributes:
        owner_uids: Accepted owners: root and, when it exists, the vakr user.
        group_gid: The vakr group, the only group allowed read access, or
            ``None`` when the host has no such group (then no group may read).
    """

    owner_uids: frozenset[int]
    group_gid: int | None

    @classmethod
    def for_host(cls) -> FilePolicy:
        """Build the production policy from this host's accounts.

        Args:
            None

        Returns:
            FilePolicy: root plus ``vakr`` as owners, ``vakr`` as reader group.

        Raises:
            None
        """
        uids = {0}
        with contextlib.suppress(KeyError):
            uids.add(pwd.getpwnam(SERVICE_USER).pw_uid)
        try:
            gid: int | None = grp.getgrnam(SERVICE_GROUP).gr_gid
        except KeyError:
            gid = None
        return cls(frozenset(uids), gid)


def file_problems(st: os.stat_result, policy: FilePolicy) -> list[str]:
    """List the policy rules a credentials file's metadata breaks.

    Args:
        st: ``lstat``/``fstat`` result of the file.
        policy: Accepted owners and reader group.

    Returns:
        list[str]: One phrase per broken rule; empty when the file is safe.

    Raises:
        None
    """
    problems = []
    mode = stat.S_IMODE(st.st_mode)
    if stat.S_ISLNK(st.st_mode):
        problems.append("file is a symbolic link; it must be a regular file")
    elif not stat.S_ISREG(st.st_mode):
        problems.append("file is not a regular file")
    if st.st_uid not in policy.owner_uids:
        problems.append(f"file is owned by uid {st.st_uid}; it must be owned by root or vakr")
    if mode & 0o022:
        problems.append(f"file is writable by group or others (mode {mode:04o}); expected 0640")
    if mode & 0o004:
        problems.append(f"file is readable by others (mode {mode:04o}); expected 0640")
    if mode & 0o040 and st.st_gid != policy.group_gid:
        problems.append(f"file is readable by group gid {st.st_gid}; only group vakr may read it")
    return problems


def directory_problems(directory: Path, st: os.stat_result, policy: FilePolicy) -> list[str]:
    """List the policy rules the credentials directory breaks.

    Args:
        directory: The directory, for the message.
        st: ``stat`` result of the directory.
        policy: Accepted owners.

    Returns:
        list[str]: One phrase per broken rule; empty when the directory is safe.

    Raises:
        None
    """
    mode = stat.S_IMODE(st.st_mode)
    problems = []
    if st.st_uid not in policy.owner_uids:
        problems.append(
            f"directory {directory} is owned by uid {st.st_uid}; it must be owned by root or vakr"
        )
    if mode & 0o022:
        problems.append(
            f"directory {directory} is writable by group or others (mode {mode:04o}); expected 0750"
        )
    return problems


def signature(directory: os.stat_result, file: os.stat_result) -> Signature:
    """Summarise what must stay unchanged for loaded credentials to stay valid.

    Args:
        directory: ``stat`` of the directory.
        file: ``stat``/``fstat`` of the file.

    Returns:
        Signature: Device, inode, and change times of both, plus file size
        and modification time. A chmod or chown changes ``ctime``, so a
        permission change also triggers a reload and a fresh policy check.

    Raises:
        None
    """
    return (
        directory.st_dev,
        directory.st_ino,
        directory.st_ctime_ns,
        file.st_dev,
        file.st_ino,
        file.st_size,
        file.st_mtime_ns,
        file.st_ctime_ns,
    )
