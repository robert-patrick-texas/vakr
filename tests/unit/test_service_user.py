"""Tests for dropping root to the ``vakr`` service user (PKG-003).

Dependencies:
    pytest: Monkeypatching ``os`` and ``pwd``.

Example:
    $ uv run pytest tests/unit/test_service_user.py
"""

import os
import pwd

import pytest

from vakr.cli import service_user


def test_non_root_changes_nothing() -> None:
    """Ordinary users keep running as themselves.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert service_user.drop_to_service_user(euid=1000) is None


def test_root_switches_groups_then_gid_then_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Root becomes the service account in the only order that works.

    Args:
        monkeypatch: Replaces privilege calls with recorders.

    Returns:
        None

    Raises:
        None
    """
    calls: list[tuple[str, object]] = []
    account = pwd.struct_passwd(("vakr", "x", 997, 996, "", "/var/lib/vakr", "/usr/sbin/nologin"))
    monkeypatch.delenv("VAKR_ROOT", raising=False)
    monkeypatch.setattr(service_user.pwd, "getpwnam", lambda _name: account)
    monkeypatch.setattr(service_user.os, "initgroups", lambda _u, g: calls.append(("groups", g)))
    monkeypatch.setattr(service_user.os, "setgid", lambda g: calls.append(("gid", g)))
    monkeypatch.setattr(service_user.os, "setuid", lambda u: calls.append(("uid", u)))
    assert service_user.drop_to_service_user(euid=0) is None
    assert calls == [("groups", 996), ("gid", 996), ("uid", 997)]
    assert os.environ["HOME"] == "/var/lib/vakr"


def test_root_without_account_or_under_test_root_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Development hosts and relocated test installs keep root as it is.

    Args:
        monkeypatch: Controls the environment and account lookup.

    Returns:
        None

    Raises:
        None
    """

    def missing(name: str) -> pwd.struct_passwd:
        """Pretend the service account does not exist.

        Args:
            name: Account name.

        Returns:
            pwd.struct_passwd: Never returns.

        Raises:
            KeyError: Always.
        """
        raise KeyError(name)

    monkeypatch.delenv("VAKR_ROOT", raising=False)
    monkeypatch.setattr(service_user.pwd, "getpwnam", missing)
    assert service_user.drop_to_service_user(euid=0) is None
    monkeypatch.setenv("VAKR_ROOT", "/srv/test")
    assert service_user.drop_to_service_user(euid=0) is None


def test_failed_switch_reports_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed privilege drop is reported, never silently ignored.

    Args:
        monkeypatch: Makes ``setgid`` fail.

    Returns:
        None

    Raises:
        None
    """
    account = pwd.struct_passwd(("vakr", "x", 997, 996, "", "/var/lib/vakr", "/usr/sbin/nologin"))

    def refuse(_gid: int) -> None:
        """Simulate a kernel refusal.

        Args:
            _gid: Requested group.

        Returns:
            None

        Raises:
            PermissionError: Always.
        """
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.delenv("VAKR_ROOT", raising=False)
    monkeypatch.setattr(service_user.pwd, "getpwnam", lambda _name: account)
    monkeypatch.setattr(service_user.os, "initgroups", lambda _u, _g: None)
    monkeypatch.setattr(service_user.os, "setgid", refuse)
    problem = service_user.drop_to_service_user(euid=0)
    assert problem is not None
    assert "Operation not permitted" in problem


def test_inventory_and_interface_commands_run_as_the_service_user(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """PKG-003: `sudo vakr inventory apply` switches to vakr before touching the database.

    Args:
        monkeypatch: Records the switch and makes it fail.
        capsys: Output capture.

    Returns:
        None

    Raises:
        None
    """
    from vakr.cli import inventory_support
    from vakr.cli.main import main

    calls: list[str] = []

    def refuse() -> str:
        """Fail the switch, as a broken service account would.

        Args:
            None

        Returns:
            str: The reason.

        Raises:
            None
        """
        calls.append("switch")
        return "cannot switch to the vakr service user: Operation not permitted"

    monkeypatch.setattr(inventory_support, "drop_to_service_user", refuse)
    assert main(["inventory", "list"]) == inventory_support.EXIT_STORAGE
    assert main(["interface", "review", "list"]) == inventory_support.EXIT_STORAGE
    assert calls == ["switch", "switch"]
    assert "cannot switch to the vakr service user" in capsys.readouterr().err
