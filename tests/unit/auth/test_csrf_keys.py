"""CSRF tokens bound to sessions and the installation key file (AUTH-003, SEC-011, E.12).

Dependencies:
    pytest; vakr.auth.csrf, keys.

Example:
    $ uv run pytest tests/unit/auth/test_csrf_keys.py
"""

import os
import stat
from pathlib import Path
from uuid import uuid7

import pytest

from vakr.auth.csrf import CsrfGuard
from vakr.auth.keys import AuthKeyError, AuthKeys, load_or_create_keys


def test_auth003_token_is_bound_to_session_and_key() -> None:
    """AUTH-003: a session's own token passes; missing, other-session, other-key tokens fail.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    keys = AuthKeys.from_master(os.urandom(32))
    guard, other_key = CsrfGuard(keys.csrf), CsrfGuard(AuthKeys.from_master(os.urandom(32)).csrf)
    mine, theirs = uuid7(), uuid7()
    token = guard.token_for(mine)
    assert len(token) == 43
    assert guard.token_for(mine) == token  # derivable on every page render
    assert guard.verify(mine, token)
    assert not guard.verify(theirs, token)
    assert not other_key.verify(mine, token)
    for bad in (None, "", token[:-1], token + "A", "x" * 1000, "é" * 43):
        assert not guard.verify(mine, bad)
    assert guard.token_for(mine) != CsrfGuard(keys.throttle).token_for(mine)


def test_keys_and_guard_reprs_hide_key_material() -> None:
    """SEC-003: neither keys nor the guard show key bytes in ``repr``.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    master = os.urandom(32)
    keys = AuthKeys.from_master(master)
    for text in (repr(keys), str(keys), repr(CsrfGuard(keys.csrf))):
        assert keys.csrf.hex() not in text
        assert master.hex() not in text
        assert repr(keys.csrf) not in text
    with pytest.raises(ValueError, match="32 bytes"):
        AuthKeys.from_master(b"short")
    with pytest.raises(ValueError, match="at least 32"):
        CsrfGuard(b"short")


def test_key_file_is_created_once_with_mode_0600(tmp_path: Path) -> None:
    """E.12: the first call creates ``auth.key`` at 0600; later calls reuse the same key.

    Args:
        tmp_path: The state directory.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "auth.key"
    first = load_or_create_keys(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(path.read_text().strip()) == 64
    assert load_or_create_keys(path) == first
    assert list(tmp_path.iterdir()) == [path]  # no temporary file left behind


def test_unsafe_or_damaged_key_file_is_refused(tmp_path: Path) -> None:
    """SEC-016 spirit: a readable-by-others, damaged, or symlinked key file is refused.

    Args:
        tmp_path: The state directory.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "auth.key"
    load_or_create_keys(path)
    path.chmod(0o640)
    with pytest.raises(AuthKeyError, match="chmod 600"):
        load_or_create_keys(path)
    path.chmod(0o600)
    path.write_text("not hex\n")
    with pytest.raises(AuthKeyError, match="damaged"):
        load_or_create_keys(path)
    target = tmp_path / "elsewhere.key"
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(OSError, match="symbolic links"):  # O_NOFOLLOW: ELOOP
        load_or_create_keys(path)
