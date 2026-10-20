"""Supporting pieces: journald events (UPD-007), commands, backups, cache, state, transport.

Dependencies:
    pytest, socket, sqlite3: Local fixtures only; no network.

Example:
    $ uv run pytest tests/unit/lifecycle/test_lifecycle_support.py
"""

import socket
import sqlite3
import struct
import tarfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.unit.lifecycle.helpers import Pki
from vakr.lifecycle.egress import castore, certinfo, transport
from vakr.lifecycle.egress.errors import EgressError
from vakr.lifecycle.egress.peek import peek_presented_certificate
from vakr.lifecycle.update import backup, cache, journal
from vakr.lifecycle.update.process import RunResult, apt_installer, run_command
from vakr.lifecycle.update.releases import asset_names
from vakr.lifecycle.update.state import StateError, UpdateState, load_state, save_state
from vakr.lifecycle.update.verify import Artifacts
from vakr.lifecycle.update.versions import Version

T0 = datetime(2026, 10, 20, 3, 15, tzinfo=UTC)


def test_journal_native_protocol(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """UPD-007: events reach journald with stable IDs; newlines use the binary form.

    Args:
        tmp_path: Temporary directory.
        monkeypatch: Pytest monkeypatch.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "journal.socket"
    receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    receiver.bind(str(path))
    monkeypatch.setattr(journal, "JOURNAL_SOCKET", path)
    printed: list[str] = []
    sink = journal.EventSink("vakr-update", echo=printed.append, echo_always=False)
    sink("update.failed", "line one\nline two", journal.PRIORITY_ERROR, version="0.3.0")
    datagram = receiver.recv(65536)
    receiver.close()
    assert b"EVENT_ID=update.failed\n" in datagram
    assert b"SYSLOG_IDENTIFIER=vakr-update\n" in datagram
    assert b"VERSION=0.3.0\n" in datagram
    message = b"line one\nline two"
    assert b"MESSAGE\n" + struct.pack("<Q", len(message)) + message in datagram
    assert printed == []
    loud = journal.EventSink("vakr-tls", echo=printed.append)
    path.unlink()
    loud("tls.ca.trusted", "trusted")
    assert printed == ["tls.ca.trusted: trusted"]
    path.write_text("not a socket")
    journal.EventSink("x", socket_path=path, echo=printed.append)("e", "m")
    assert printed[-1] == "e: m"


def test_journal_send_failure_is_tolerated(tmp_path: Path) -> None:
    """A journald socket that refuses the datagram falls back to the console.

    Args:
        tmp_path: Temporary directory.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "dead.socket"
    dead = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    dead.bind(str(path))
    dead.close()  # the socket file stays, but nobody listens
    printed: list[str] = []
    journal.EventSink("x", socket_path=path, echo=printed.append)("e", "m")
    assert printed == ["e: m"]


def test_run_command_and_apt_installer(tmp_path: Path) -> None:
    """Commands run as argument lists; missing programs and timeouts become exit codes.

    Args:
        tmp_path: Temporary directory.

    Returns:
        None

    Raises:
        None
    """
    assert run_command(["/bin/sh", "-c", "cat; echo err >&2"], stdin=b"in").stdout == "in"
    assert run_command(["/bin/sh", "-c", "echo $X"], env={"X": "y"}).stdout.strip() == "y"
    assert run_command([str(tmp_path / "absent")]).returncode == 127
    slow = run_command(["/bin/sleep", "5"], timeout=0.1)
    assert slow.returncode == 124
    assert "timed out" in slow.tail()
    assert RunResult(1, "a\nb\nc", "").tail(2) == "b\nc"
    seen: list[tuple[list[str], dict[str, str] | None]] = []

    def fake(argv, *, stdin=None, env=None, timeout=900.0) -> RunResult:
        """Record the installer call.

        Args:
            argv: Command.
            stdin: Unused.
            env: Environment.
            timeout: Unused.

        Returns:
            RunResult: Success.

        Raises:
            None
        """
        del stdin, timeout
        seen.append((list(argv), env))
        return RunResult(0)

    package = tmp_path / "vakr_0.3.0_amd64.deb"
    assert apt_installer(fake)(package).returncode == 0
    argv, env = seen[0]
    assert argv == ["/usr/bin/apt-get", "install", "-y", "--allow-downgrades", str(package)]
    assert env == {"DEBIAN_FRONTEND": "noninteractive"}


def _db(path: Path, value: str) -> None:
    """Create a one-row SQLite database.

    Args:
        path: Database file.
        value: Row value.

    Returns:
        None

    Raises:
        None
    """
    with sqlite3.connect(path) as db:
        db.execute("create table t (v text)")
        db.execute("insert into t values (?)", (value,))
    db.close()


def test_backup_restore_prune_and_naming(tmp_path: Path) -> None:
    """UPD-002/UPD-005: backups snapshot config and DBs, restore exactly, keep 5.

    Args:
        tmp_path: Temporary directory.

    Returns:
        None

    Raises:
        None
    """
    config, state, store = tmp_path / "etc", tmp_path / "state", tmp_path / "backups"
    (config / "conf.d").mkdir(parents=True)
    (config / "conf.d/10.toml").write_text("a = 1\n")
    state.mkdir()
    _db(state / "meta.db", "before")
    (state / "odd.db").write_bytes(b"not sqlite")
    archive = backup.create_backup(config, state, store, T0)
    assert archive.name == "pre-update-20261020T031500Z.tar.gz"
    assert archive.stat().st_mode & 0o777 == 0o600
    assert backup.create_backup(config, state, store, T0).name.endswith("Z-1.tar.gz")
    (config / "conf.d/10.toml").write_text("a = 2\n")
    (config / "new.toml").write_text("added later\n")
    (state / "meta.db").unlink()
    _db(state / "meta.db", "after")
    (state / "meta.db-shm").write_bytes(b"x")
    backup.restore_backup(archive, config, state)
    assert (config / "conf.d/10.toml").read_text() == "a = 1\n"
    assert not (config / "new.toml").exists()
    with sqlite3.connect(state / "meta.db") as db:
        assert db.execute("select v from t").fetchall() == [("before",)]
    db.close()
    assert (state / "odd.db").read_bytes() == b"not sqlite"
    assert not (state / "meta.db-shm").exists()
    for minutes in range(1, 7):
        backup.create_backup(config, state, store, T0 + timedelta(minutes=minutes))
    assert len(backup.list_backups(store)) == 8
    assert len(backup.prune_backups(store, keep=5)) == 3
    assert len(backup.list_backups(store)) == 5
    assert backup.list_backups(tmp_path / "absent") == []
    empty = backup.create_backup(tmp_path / "no-config", tmp_path / "no-state", store, T0)
    _db(state / "big.db", "large")
    listed = backup.create_backup(config, state, store, T0, names=("meta.db", "absent.db"))
    with tarfile.open(listed) as tar:
        assert sorted(n for n in tar.getnames() if n.startswith("state/")) == ["state/meta.db"]
    backup.restore_backup(empty, tmp_path / "no-config", tmp_path / "no-state")
    assert not (tmp_path / "no-config").exists()


def test_cache_prune_keeps_installed_and_two_older(tmp_path: Path) -> None:
    """E.14: after an update, the installed version and two older ones stay cached.

    Args:
        tmp_path: Temporary directory.

    Returns:
        None

    Raises:
        None
    """
    root = tmp_path / "updates"
    for name in ("0.2.0", "0.3.0-rc.1", "0.3.0", "0.4.0", "0.5.0", "notes"):
        (root / name).mkdir(parents=True)
    (root / "0.9.0").write_text("a file, not a version directory")
    removed = cache.prune(root, Version.parse("0.4.0"))
    assert removed == [Version.parse("0.2.0"), Version.parse("0.5.0")]
    remaining = sorted(p.name for p in root.iterdir())
    assert remaining == ["0.3.0", "0.3.0-rc.1", "0.4.0", "0.9.0", "notes"]
    assert cache.prune(root, Version.parse("0.4.0"), keep_previous=0) == [
        Version.parse("0.3.0-rc.1"),
        Version.parse("0.3.0"),
    ]
    assert cache.cached_versions(tmp_path / "absent") == []
    names = asset_names("vakr", Version.parse("0.4.0"), "amd64")
    assert cache.cached(root, Version.parse("0.4.0"), names) is None
    source = tmp_path / "src"
    source.mkdir()
    for name in ("renamed.deb", names.sums, names.signature):
        (source / name).write_text(name)
    files = Artifacts(source / "renamed.deb", source / names.sums, source / names.signature)
    with cache.staging(root) as staged_dir:
        staged = cache.copy_in(files, staged_dir, names)
        assert staged.package.name == names.package
        stored = cache.promote(staged, root, Version.parse("0.4.0"))
    assert not staged_dir.exists()
    assert cache.cached(root, Version.parse("0.4.0"), names) == stored
    assert stored.package.read_text() == "renamed.deb"
    (root / ".incoming-stale").mkdir()
    cache.prune(root, Version.parse("0.4.0"))
    assert not (root / ".incoming-stale").exists()


def test_state_round_trip_and_corruption(tmp_path: Path) -> None:
    """UPD-006: the state file round-trips and corruption is reported, not ignored.

    Args:
        tmp_path: Temporary directory.

    Returns:
        None

    Raises:
        None
    """
    path = tmp_path / "state/update-state.json"
    assert load_state(path) == UpdateState()
    record = UpdateState("failed", "t", "0.2.0", "0.3.0", "m", ("0.3.0",), "/b.tar.gz")
    save_state(path, record)
    assert load_state(path) == record
    assert path.stat().st_mode & 0o777 == 0o644
    path.write_text("{")
    with pytest.raises(StateError, match="cannot read"):
        load_state(path)
    path.write_text("[]")
    with pytest.raises(StateError, match="not an update state"):
        load_state(path)


def _reply(data: bytes, close: bool = False) -> socket.socket:
    """Return a socket whose peer already sent ``data`` as a proxy reply.

    Args:
        data: Bytes the fake proxy sends.
        close: Close the peer after sending.

    Returns:
        socket.socket: The client end.

    Raises:
        None
    """
    client, peer = socket.socketpair()
    client.settimeout(2)
    peer.sendall(data)
    if close:
        peer.shutdown(socket.SHUT_WR)  # EOF for the client, but our request still lands
    threading.Timer(3, peer.close).start()
    return client


@pytest.mark.parametrize(
    ("reply", "close", "message"),
    [
        (b"HTTP/1.1 200 OK\r\n\r\nEXTRA", False, "unexpected data"),
        (b"garbage here\r\n\r\n", False, "malformed CONNECT reply"),
        (b"HTTP/1.1 200", True, "closed the connection"),
        (b"X" * (transport.MAX_PROXY_REPLY + 5000), False, "too large"),
        (b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n", False, "refused CONNECT"),
    ],
)
def test_proxy_reply_parsing(reply: bytes, close: bool, message: str) -> None:
    """A misbehaving proxy cannot inject bytes or hide a refusal.

    Args:
        reply: Fake proxy reply.
        close: Whether the proxy closes after replying.
        message: Expected error text.

    Returns:
        None

    Raises:
        None
    """
    sock = _reply(reply, close)
    with pytest.raises(EgressError, match=message) as exc:
        transport.proxy_connect(sock, "::1", 443)
    assert exc.value.step == "proxy"
    sock.close()


def test_proxy_io_error_and_peek_failure(tmp_path: Path, pki: Pki) -> None:
    """Socket errors during CONNECT and failed peeks are reported as egress errors.

    Args:
        tmp_path: Temporary directory.
        pki: Test certificates.

    Returns:
        None

    Raises:
        None
    """
    client, peer = socket.socketpair()
    peer.close()
    client.close()
    with pytest.raises(EgressError, match="CONNECT"):
        transport.proxy_connect(client, "host", 443)
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]

    def reject() -> None:
        """Accept one connection and close it without TLS.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        conn, _ = listener.accept()
        conn.close()

    threading.Thread(target=reject, daemon=True).start()
    with pytest.raises(EgressError, match="handshake"):
        peek_presented_certificate("127.0.0.1", port, None, 2.0)
    listener.close()
    with pytest.raises(certinfo.CertificateRejectedError):
        certinfo.summarize_der(b"not der")
    assert certinfo.expiry_state(T0, T0 - timedelta(days=40)) == "ok"
    store = castore.SystemStore(tmp_path, ("/bin/false",))
    (tmp_path / "ca.crt").write_bytes(pki.ca.pem)
    assert castore.remove_system("ca", b"different", store, lambda _argv: 0) is False
    with pytest.raises(RuntimeError, match="exited 1"):
        castore.remove_system("ca", pki.ca.pem, store, lambda _argv: 1)
