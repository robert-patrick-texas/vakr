"""Tests for the ``ping_group_range`` preflight and its exact fix (ICMP-012).

Each test builds a small filesystem root with ``/proc/sys`` and sysctl.d
files. The central case is this project's development host: an admin file
``/etc/sysctl.d/99-allow-ping.conf`` narrows the range to ``1000 9999`` and
sysusers gives the ``vakr`` group a GID below 1000, so a default install is
refused and the fix must widen the range in a drop-in that sorts later.

Dependencies:
    pwd, pytest: Account records and temporary directories.
    vakr.adapters.icmp.sysctl, health: The functions under test.

Example:
    $ uv run pytest tests/unit/adapters/icmp/test_icmp_preflight.py
"""

import pwd
from pathlib import Path

import pytest

from vakr.adapters.icmp import health, sysctl
from vakr.adapters.icmp.health import Identity, collector_identity, evaluate
from vakr.health.model import Status
from vakr.health.registry import CHECKS, run_checks

SERVICE = Identity("the vakr service user", (987,), service=True)
SYSTEMD_DEFAULT = "-net.ipv4.ping_group_range = 0 2147483647\n"


def make_root(tmp_path: Path, running: str, files: dict[str, str]) -> Path:
    """Create a root with the running value and some sysctl files.

    Args:
        tmp_path: Pytest temporary directory.
        running: Contents of ``/proc/sys/net/ipv4/ping_group_range``.
        files: Relative path to file contents.

    Returns:
        Path: The root.

    Raises:
        None
    """
    for relative, text in {sysctl.PROC_FILE: running, **files}.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def no_socket_problem() -> str | None:
    """Pretend the ICMP socket opened.

    Args:
        None

    Returns:
        str | None: ``None``.

    Raises:
        None
    """
    return None


def test_icmp_012_dev_host_default_install_gets_a_later_drop_in(tmp_path: Path) -> None:
    """ICMP-012: GID 987 against ``1000 9999`` gives the exact drop-in and sysctl --system.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    root = make_root(
        tmp_path,
        "1000\t9999\n",
        {
            "usr/lib/sysctl.d/50-default.conf": SYSTEMD_DEFAULT,
            "etc/sysctl.d/99-allow-ping.conf": "# local\nnet.ipv4.ping_group_range = 1000 9999\n",
        },
    )
    result = evaluate(sysctl.read_range(root), SERVICE, root, no_socket_problem)
    assert result.status is Status.WARNING
    assert "the vakr service user (GID 987) is outside net.ipv4.ping_group_range 1000 9999" in (
        result.summary
    )
    assert result.summary.endswith(
        "fix: echo 'net.ipv4.ping_group_range = 987 9999' | "
        "sudo tee /etc/sysctl.d/99-vakr-icmp.conf && sudo sysctl --system"
    )


@pytest.mark.parametrize(
    ("relative", "line"),
    [("etc/sysctl.conf", 2), ("etc/sysctl.d/99-zz-hardening.conf", 1)],
    ids=["sysctl.conf is read last", "a file named after the drop-in"],
)
def test_a_setting_read_after_the_drop_in_must_be_edited_instead(
    tmp_path: Path, relative: str, line: int
) -> None:
    """A drop-in that would be overridden is not offered; the overriding file is named.

    Args:
        tmp_path: Pytest temporary directory.
        relative: The overriding file.
        line: Line that sets the key in it.

    Returns:
        None

    Raises:
        None
    """
    body = "\n" * (line - 1) + "net/ipv4/ping_group_range=1000 9999\n"
    root = make_root(tmp_path, "1000 9999", {relative: body})
    fix = sysctl.plan_fix(sysctl.read_range(root), 987, root)
    assert fix.override == sysctl.Setting(root / relative, line, "1000 9999")
    assert fix.text() == (
        f"edit {root / relative} line {line} (read after any drop-in) to read "
        "'net.ipv4.ping_group_range = 987 9999', then run sudo sysctl --system"
    )


def test_ranges_widen_without_narrowing_anyone() -> None:
    """The fix only ever adds the GID; the kernel's empty ``1 0`` becomes just that GID.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert str(sysctl.GroupRange(1000, 9999).widened(987)) == "987 9999"
    assert str(sysctl.GroupRange(0, 500).widened(987)) == "0 987"
    assert str(sysctl.GroupRange(1, 0).widened(987)) == "987 987"
    assert "987" not in sysctl.GroupRange(0, 2_147_483_647)
    assert 987 in sysctl.GroupRange(0, 2_147_483_647)


def test_sysctl_files_follow_procps_order_and_precedence(tmp_path: Path) -> None:
    """/etc overrides /usr/lib by name, files apply sorted by name, sysctl.conf last.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    root = make_root(
        tmp_path,
        "0 0",
        {
            "usr/lib/sysctl.d/50-default.conf": SYSTEMD_DEFAULT,
            "etc/sysctl.d/50-default.conf": "# masked by the admin\n",
            "run/sysctl.d/10-early.conf": " ; comment\n net.ipv4.ping_group_range = 1 1\n",
            "lib/sysctl.d/70-late.conf": "net.ipv4.ping_group_range = 2 2\n# x = y\n",
            "etc/sysctl.conf": "net.ipv4.ip_forward = 1\nnet.ipv4.ping_group_range = 3 3\n",
        },
    )
    values = [s.value for s in sysctl.find_settings(root)]
    assert values == ["1 1", "2 2", "3 3"]


def test_unreadable_and_oversized_files_are_skipped(tmp_path: Path) -> None:
    """A file the check cannot read, or a huge one, never breaks the check.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    big = "#" * (sysctl.MAX_FILE_BYTES + 1)
    root = make_root(
        tmp_path,
        "0 0",
        {"etc/sysctl.d/10-big.conf": big, "etc/sysctl.d/20-locked.conf": SYSTEMD_DEFAULT},
    )
    (root / "etc/sysctl.d/20-locked.conf").chmod(0)
    try:
        assert sysctl.find_settings(root) == []
    finally:
        (root / "etc/sysctl.d/20-locked.conf").chmod(0o600)


@pytest.mark.parametrize("text", ["", "1000", "a b", "1 2 3"])
def test_malformed_ranges_are_rejected(text: str) -> None:
    """The kernel always writes two integers; anything else is reported, not guessed.

    Args:
        text: Malformed contents.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(ValueError, match=r"."):
        sysctl.parse_range(text)


def test_unreadable_sysctl_is_unknown(tmp_path: Path) -> None:
    """Without ``/proc`` the check cannot know, so it says UNKNOWN.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    result = health.check_icmp(tmp_path)
    assert result.status is Status.UNKNOWN
    assert "FileNotFoundError" in result.summary


def test_allowed_service_group_is_ok_without_a_socket_test(tmp_path: Path) -> None:
    """On an installed node the socket test would test the wrong user, so it is skipped.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    root = make_root(tmp_path, "0 2147483647", {})

    def failing() -> str | None:
        """Fail if called.

        Args:
            None

        Returns:
            str | None: Never returns.

        Raises:
            AssertionError: Always.
        """
        raise AssertionError("socket test must not run for the service identity")

    result = evaluate(sysctl.read_range(root), SERVICE, root, failing)
    assert result.status is Status.OK
    assert result.summary == (
        "unprivileged ICMP allowed for the vakr service user "
        "(GID 987 in net.ipv4.ping_group_range 0 2147483647)"
    )


def test_development_identity_also_opens_a_socket(tmp_path: Path) -> None:
    """In a checkout the range may admit us while something else (seccomp) still refuses.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    root = make_root(tmp_path, "1000 9999", {})
    me = Identity("this process", (1000, 4), service=False)
    ok = evaluate(sysctl.read_range(root), me, root, no_socket_problem)
    refused = evaluate(sysctl.read_range(root), me, root, lambda: "Permission denied")
    assert ok.status is Status.OK
    assert refused.status is Status.WARNING
    assert refused.summary.endswith("an ICMP socket failed: Permission denied")


def test_installed_cap_net_raw_fallback_is_reported_ok(tmp_path: Path) -> None:
    """The documented collector-only fallback is a valid choice, so it is OK, and named.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    dropin = f"{health.FALLBACK_DIR}/50-icmp-cap-net-raw.conf"
    commented = f"{health.FALLBACK_DIR}/10-notes.conf"
    root = make_root(
        tmp_path,
        "1000 9999",
        {
            commented: "# AmbientCapabilities=CAP_NET_RAW\n",
            dropin: "AmbientCapabilities=CAP_NET_RAW\n",
        },
    )
    result = evaluate(sysctl.read_range(root), SERVICE, root, no_socket_problem)
    assert result.status is Status.OK
    assert result.summary.endswith(f"using the CAP_NET_RAW fallback from {root / dropin}")


def test_identity_is_the_service_account_only_on_an_installed_node() -> None:
    """The ``vakr`` GID is checked on a node; the process's groups in a checkout.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    account = pwd.struct_passwd(("vakr", "x", 990, 987, "", "/var/lib/vakr", "/usr/sbin/nologin"))

    def found(name: str) -> pwd.struct_passwd:
        """Return the fake account.

        Args:
            name: Account name.

        Returns:
            pwd.struct_passwd: The account.

        Raises:
            None
        """
        assert name == "vakr"
        return account

    def missing(name: str) -> pwd.struct_passwd:
        """Report no such account.

        Args:
            name: Account name.

        Returns:
            pwd.struct_passwd: Never returns.

        Raises:
            KeyError: Always.
        """
        raise KeyError(name)

    assert collector_identity({}, found) == SERVICE
    assert collector_identity({"VAKR_ROOT": "/srv/t"}, found).service is False
    assert collector_identity({}, missing).label == "this process"


def test_check_is_registered_for_doctor_and_check_health() -> None:
    """ICMP-012: ``vakr doctor`` and ``vakr check health`` both run the ICMP check.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert health.check_icmp in CHECKS
    assert "icmp" in [r.name for r in run_checks()]


def test_socket_test_reports_the_kernel_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The development socket test names the error the kernel returned.

    Args:
        monkeypatch: Pytest monkeypatch.

    Returns:
        None

    Raises:
        None
    """

    def refuse(*_args: object) -> None:
        """Refuse like a kernel outside the range.

        Args:
            *_args: Ignored.

        Returns:
            None

        Raises:
            PermissionError: Always.
        """
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(health.socket, "socket", refuse)
    assert health.socket_problem() == "Permission denied"


def test_unreadable_unit_dropins_are_skipped(tmp_path: Path) -> None:
    """A drop-in the check cannot read never counts as the fallback.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        None

    Raises:
        None
    """
    locked = tmp_path / health.FALLBACK_DIR / "50-icmp-cap-net-raw.conf"
    locked.parent.mkdir(parents=True)
    locked.write_text("AmbientCapabilities=CAP_NET_RAW\n")
    locked.chmod(0)
    try:
        assert health.fallback_dropin(tmp_path) is None
    finally:
        locked.chmod(0o600)
