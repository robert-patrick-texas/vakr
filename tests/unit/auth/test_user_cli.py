"""CLI: ``vakr setup`` and ``vakr user`` (SEC-005, SEC-006, SEC-010, SEC-018, CRED-002).

Runs the real commands against a relocated install (``VAKR_ROOT``) with a
migrated SQLite metadata database. Passwords are invented per run and fed
through a fake hidden prompt; afterwards the captured output, every audit
row, and the raw database files are searched for them (CRED-002). Argon2
gets cheap parameters by injection only.

Dependencies:
    pytest; vakr.cli.main; the SQLite metadata adapter.

Example:
    $ uv run pytest tests/unit/auth/test_user_cli.py
"""

import io
import json
import stat
from pathlib import Path

import pytest

from tests.unit.auth.auth_testkit import cheap_hasher, new_password
from vakr.adapters.sqlite.metadata import (
    METADATA_FILE,
    SqliteMetadataStore,
    metadata_migrations,
    sqlite_uow_factory,
)
from vakr.auth.passwords import Passwords
from vakr.cli import user_support
from vakr.cli.main import main
from vakr.domain.paging import PageRequest


class Prompts:
    """A fake hidden prompt: answers queued per test, prompts recorded.

    Attributes:
        answers: What the operator "types", in order.
        asked: Every prompt shown.
    """

    def __init__(self) -> None:
        """Start with no answers.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.answers: list[str] = []
        self.asked: list[str] = []

    def __call__(self, prompt: str = "") -> str:
        """Answer one prompt.

        Args:
            prompt: The prompt text.

        Returns:
            str: The next queued answer.

        Raises:
            EOFError: When no answer is queued, like a closed terminal.
        """
        self.asked.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


class FakeTerminal(io.StringIO):
    """Standard input that claims to be a terminal, so the hidden prompt is used."""

    def isatty(self) -> bool:
        """Claim to be a terminal.

        Args:
            None

        Returns:
            bool: ``True``.

        Raises:
            None
        """
        return True


@pytest.fixture
def prompts(monkeypatch: pytest.MonkeyPatch) -> Prompts:
    """Replace the hidden prompt, present a terminal, and use cheap Argon2 parameters.

    Args:
        monkeypatch: Patches ``getpass``, ``sys.stdin``, and the hasher factory.

    Returns:
        Prompts: The fake prompt.

    Raises:
        None
    """
    fake = Prompts()
    monkeypatch.setattr("getpass.getpass", fake)
    monkeypatch.setattr("sys.stdin", FakeTerminal())
    monkeypatch.setattr(user_support, "Passwords", lambda: Passwords(cheap_hasher()))
    return fake


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Relocate the install and migrate an empty metadata database.

    Args:
        tmp_path: Install root.
        monkeypatch: Sets ``VAKR_ROOT`` and a sudo-like environment.

    Returns:
        Path: The install root.

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    for name in ("LOGNAME", "USER"):
        monkeypatch.setenv(name, "root")
    monkeypatch.setenv("SUDO_USER", "opsadmin")
    (tmp_path / "var/lib/vakr").mkdir(parents=True)
    metadata_migrations(tmp_path / "var/lib/vakr" / METADATA_FILE).migrate()
    return tmp_path


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    """Run ``vakr`` and return its exit code and combined output.

    Args:
        capsys: Captures output.
        *argv: Arguments.

    Returns:
        tuple[int, str]: Exit code and stdout plus stderr.

    Raises:
        None
    """
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out + out.err


def _leaks(root: Path, secrets: list[str]) -> list[str]:
    """Find secrets in audit rows and in the raw database files.

    Args:
        root: Install root.
        secrets: Plain passwords to look for.

    Returns:
        list[str]: Where each secret was found; empty when nowhere.

    Raises:
        None
    """
    db = root / "var/lib/vakr" / METADATA_FILE
    store = SqliteMetadataStore(db)
    with sqlite_uow_factory(store)() as uow:
        audit = repr(uow.audit.list_page(PageRequest(limit=500)).items)
    store.close()
    found = [f"audit:{n}" for n, s in enumerate(secrets) if s in audit]
    for path in db.parent.glob(f"{METADATA_FILE}*"):
        data = path.read_bytes()
        found += [f"{path.name}:{n}" for n, s in enumerate(secrets) if s.encode() in data]
    return found


def test_setup_creates_the_first_admin_and_refuses_a_second(
    root: Path, prompts: Prompts, capsys: pytest.CaptureFixture[str]
) -> None:
    """SEC-005, 27.4: setup creates key and admin, prints next steps, then refuses to repeat.

    Args:
        root: Install root.
        prompts: The fake hidden prompt.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    pw, pw2 = new_password(), new_password()
    prompts.answers = [pw, pw]
    code, text = _run(capsys, "setup", "--username", "Alice")
    assert code == 0, text
    assert "Created admin account alice." in text
    for words in ("Next steps", "credentials.toml", "inventory plan", "vakr-collector", "vakr-api"):
        assert words in text
    key = root / "var/lib/vakr/auth.key"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert prompts.asked == ["New password for alice: ", "Repeat the password: "]
    code, text = _run(capsys, "setup", "--username", "bob")
    assert code == 1
    assert "already exists" in text
    assert len(prompts.asked) == 2  # refused before asking for a password
    prompts.answers = [pw2, pw2]
    assert _run(capsys, "setup", "--add-admin", "--username", "bob")[0] == 0
    code, text = _run(capsys, "user", "list", "--json")
    users = json.loads(text)["users"]
    assert [(u["username"], u["role"]) for u in users] == [("alice", "admin"), ("bob", "admin")]
    assert all("password_hash" not in u for u in users)
    assert "$argon2id$" not in text
    assert _leaks(root, [pw, pw2]) == []


def test_setup_needs_the_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompts: Prompts,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit 4 and the `vakr db migrate` hint when no metadata database exists.

    Args:
        tmp_path: Install root without a database.
        monkeypatch: Sets ``VAKR_ROOT``.
        prompts: The fake hidden prompt.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setenv("VAKR_ROOT", str(tmp_path))
    code, text = _run(capsys, "setup", "--username", "alice")
    assert code == 4
    assert "vakr db migrate" in text
    assert prompts.asked == []


def test_setup_retries_bad_entries_then_gives_up_without_echoing(
    root: Path, prompts: Prompts, capsys: pytest.CaptureFixture[str]
) -> None:
    """E.12: a short password and two mismatches use up the attempts; nothing is created.

    Args:
        root: Install root.
        prompts: The fake hidden prompt.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    good, short = new_password(), "short-pw"
    prompts.answers = [short, good, good + "x", good, "different-" + good]
    code, text = _run(capsys, "setup", "--username", "alice")
    assert code == 1
    assert "at least 15 characters" in text
    assert text.count("do not match") == 2
    assert "no acceptable password" in text
    for typed in (short, good):
        assert typed not in text
    assert _run(capsys, "user", "list")[1].startswith("no accounts")
    assert _leaks(root, [short, good]) == []


def test_setup_asks_for_the_username_and_defaults_to_admin(
    root: Path,
    prompts: Prompts,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """27.4: without ``--username`` setup asks, offering ``admin``.

    Args:
        root: Install root.
        prompts: The fake hidden prompt.
        monkeypatch: Answers the username prompt.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    pw = new_password()
    prompts.answers = [pw, pw]
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    code, text = _run(capsys, "setup")
    assert code == 0, text
    assert "Created admin account admin." in text
    assert _leaks(root, [pw]) == []


def test_user_commands_manage_accounts_with_audit(
    root: Path, prompts: Prompts, capsys: pytest.CaptureFixture[str]
) -> None:
    """SEC-005, SEC-006, SEC-018: add, passwd, disable, enable, role, revoke; last admin kept.

    Args:
        root: Install root.
        prompts: The fake hidden prompt.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    typed = [new_password() for _ in range(3)]
    prompts.answers = [typed[0], typed[0]]
    assert _run(capsys, "setup", "--username", "alice")[0] == 0
    prompts.answers = [typed[1], typed[1]]
    code, text = _run(capsys, "user", "add", "Bob", "--role", "operator", "--display-name", "Bob")
    assert (code, text.strip()) == (0, "added operator account bob")
    assert _run(capsys, "user", "add", "bob", "--role", "admin")[0] == 1  # exists; no prompt
    prompts.answers = [typed[2], typed[2]]
    assert "0 session(s) ended" in _run(capsys, "user", "passwd", "bob")[1]
    steps = [
        (("disable", "bob"), 0, "bob: disabled"),
        (("enable", "bob"), 0, "bob: enabled"),
        (("role", "bob", "admin"), 0, "role is now admin"),
        (("revoke-sessions", "bob"), 0, "ended 0 session(s)"),
        (("disable", "alice"), 0, "alice: disabled"),
        (("disable", "bob"), 1, "at least one enabled admin"),
        (("role", "bob", "operator"), 1, "at least one enabled admin"),
        (("disable", "ghost"), 1, "user ghost does not exist"),
        (("passwd", "ghost"), 1, "no account ghost"),
    ]
    for argv, want, words in steps:
        code, text = _run(capsys, "user", *argv)
        assert (code, words in text) == (want, True), (argv, text)
    rows = {
        line.split()[0]: line.split()[1:3] for line in _run(capsys, "user", "list")[1].splitlines()
    }
    assert (rows["alice"], rows["bob"]) == (["admin", "disabled"], ["admin", "enabled"])
    store = SqliteMetadataStore(root / "var/lib/vakr" / METADATA_FILE)
    with sqlite_uow_factory(store)() as uow:
        events = uow.audit.list_page(PageRequest(limit=100)).items
    store.close()
    assert {e.actor for e in events} == {"cli:opsadmin (sudo)"}
    assert len(events) == 8  # 2 creates, passwd, disable, enable, role, revoke, disable
    assert _leaks(root, typed) == []


@pytest.mark.usefixtures("root")
def test_passwords_are_never_command_line_arguments(
    prompts: Prompts, capsys: pytest.CaptureFixture[str]
) -> None:
    """SEC-003: there is no password option; a usage error results, and no prompt runs.

    Args:
        prompts: The fake hidden prompt.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(SystemExit) as caught:
        main(["user", "add", "bob", "--role", "operator", "--password", new_password()])
    assert caught.value.code == 2
    with pytest.raises(SystemExit):
        main(["setup", "--password", "x"])
    capsys.readouterr()
    assert _run(capsys, "user")[0] == 2
    assert prompts.asked == []


def test_piped_passwords_are_read_from_stdin_without_a_username_prompt(
    root: Path,
    prompts: Prompts,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without a terminal, each password is one stdin line and setup never reads a username.

    Args:
        root: Install root.
        prompts: The fake hidden prompt, which must stay unused.
        monkeypatch: Replaces stdin with a pipe.
        capsys: Captures output.

    Returns:
        None

    Raises:
        None
    """
    pw = new_password()
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{pw}\n{pw}\n"))
    code, text = _run(capsys, "setup")
    assert code == 0, text
    assert "Created admin account admin." in text
    assert prompts.asked == []
    assert pw not in text
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{pw}\n"))  # the repeat line is missing
    code, text = _run(capsys, "user", "add", "bob", "--role", "operator")
    assert (code, "cancelled" in text) == (1, True)
    assert _leaks(root, [pw]) == []
