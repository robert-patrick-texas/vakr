"""CRED-002: the redaction corpus; no forced failure may reveal a secret.

Every test generates unique fake secrets, forces a failure or an output path,
and searches everything an operator or a log could see: exception ``str`` and
``repr``, formatted tracebacks with chained causes, tracebacks with captured
local variables, CLI output, health-check summaries, log output through the
standard ``logging`` module, ``repr``/``str``/f-strings of every type, and
``json``/``pickle`` attempts (SEC-003, SEC-020). The API, diagnostics,
support-bundle, and audit payloads do not exist yet; they must reuse
``Secret`` and ``redact_text`` and extend this corpus when they arrive.

Dependencies:
    pytest: Fixtures and output capture.
    logging, json, pickle, traceback: The leak paths under test.

Example:
    $ uv run pytest tests/unit/credentials/test_redaction_corpus.py
"""

import dataclasses
import io
import json
import logging
import pickle
import secrets

import pytest

from tests.unit.credentials.credentials_testkit import (
    FakeSecrets,
    Install,
    assert_no_secret,
    failure_text,
    own_policy,
    v2c_table,
)
from vakr.cli.main import main
from vakr.credentials.health import check_credentials
from vakr.credentials.model import SnmpV2cCredential
from vakr.credentials.parse import parse_credentials
from vakr.credentials.protected import read_protected
from vakr.credentials.provider import FileCredentialProvider
from vakr.credentials.redact import MASK, RedactingFilter, redact_text
from vakr.credentials.secret import Secret
from vakr.credentials.vocabulary import CredentialReference

REF = CredentialReference("network-v2c")
# Each template breaks the file on a line that holds the secret {s}.
SYNTAX_TEMPLATES = (
    'community = "{s}',  # unterminated at end of file
    'community = "{s}\n',  # newline inside a basic string
    "community = {s}\n",  # bare value
    'community = "{s}" trailing\n',  # junk after the value
    'community = "{s}\x01"\n',  # control character inside the secret
    "{s}\n",  # secret pasted on a line of its own
    'community = """{s}',  # unterminated multi-line string
    "community = '''{s}\n",  # unterminated multi-line literal
    'community = "{s}"\ncommunity = "{s}"\n',  # duplicate key
    'community = [ "{s}", \n',  # unclosed array
    'community = {{ v = "{s}" \n',  # unclosed inline table
    'community = "\\q{s}"\n',  # invalid escape before the secret
)


def _observe(install: Install, capsys: pytest.CaptureFixture[str]) -> str:
    """Collect everything a failure exposes: exception renderings, CLI, and health.

    Args:
        install: The relocated install holding the broken file.
        capsys: Captures CLI output.

    Returns:
        str: All renderings joined.

    Raises:
        None
    """
    provider = FileCredentialProvider(install.secrets_dir, policy=own_policy())
    rendered = [failure_text(lambda: provider.resolve(REF)), repr(provider)]
    rendered.append(failure_text(lambda: provider.secret("metadata-dsn")))
    main(["credentials", "check"])
    main(["credentials", "list", "--dev"])
    captured = capsys.readouterr()
    rendered += [captured.out, captured.err, repr(check_credentials())]
    return "\n".join(rendered)


@pytest.mark.parametrize("template", SYNTAX_TEMPLATES)
@pytest.mark.usefixtures("host_policy")
def test_cred_002_syntax_error_on_a_secret_line(
    install: Install,
    fake: FakeSecrets,
    capsys: pytest.CaptureFixture[str],
    template: str,
) -> None:
    """TOML syntax errors on the line holding a secret report the line, never the text.

    Args:
        install: The relocated install.
        fake: Fake secret values.
        capsys: Captures CLI output.
        template: Broken line with ``{s}`` for the secret.

    Returns:
        None

    Raises:
        None
    """
    broken = template.format(s=fake.community)
    install.write(f'{fake.file_text()}\n[credentials.broken]\nversion = "2c"\n{broken}')
    seen = _observe(install, capsys)
    assert "TOML syntax error" in seen
    assert_no_secret(seen, fake.values())


@pytest.mark.usefixtures("host_policy")
def test_cred_002_invalid_utf8_next_to_a_secret(
    install: Install, fake: FakeSecrets, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-UTF-8 byte inside a secret reports the line only.

    Args:
        install: The relocated install.
        fake: Fake secret values.
        capsys: Captures CLI output.

    Returns:
        None

    Raises:
        None
    """
    tail = f'[credentials.b]\nversion = "2c"\ncommunity = "{fake.community}\xff"\n'
    install.write(fake.file_text().encode() + tail.encode("latin-1"))
    seen = _observe(install, capsys)
    assert "not valid UTF-8" in seen
    assert_no_secret(seen, fake.values())


@pytest.mark.parametrize(
    "body",
    [
        'version = "2c"\ncommunity = {n}\n',  # numeric community: wrong type
        'version = "2c"\ncommunity = [ "{s}" ]\n',  # array
        'version = "2c"\ncommunity = {{ v = "{s}" }}\n',  # inline table
        'version = "2c"\ncomunity = "{s}"\n',  # misspelled key, missing field
        'version = "{s}"\ncommunity = "{s}"\n',  # invalid version value
        'version = "3"\nusername = "u"\nsecurity_level = "{s}"\n',  # invalid choice value
        'version = "3"\nusername = "u"\nsecurity_level = "authPriv"\n'
        'auth_protocol = "SHA256"\nauth_secret = "{s}"\n',  # missing privacy fields
        'version = "3"\nusername = "u"\nsecurity_level = "noAuthNoPriv"\n'
        'auth_secret = "{s}"\n',  # secret not allowed for the level
        'version = "3"\nusername = "u"\nsecurity_level = "authNoPriv"\n'
        'auth_protocol = "SHA"\nauth_secret = "{short}"\n',  # passphrase too short
    ],
)
@pytest.mark.usefixtures("host_policy")
def test_cred_002_wrong_types_and_missing_fields(
    install: Install,
    fake: FakeSecrets,
    capsys: pytest.CaptureFixture[str],
    body: str,
) -> None:
    """Validation failures name the key and rule; no value appears anywhere.

    Args:
        install: The relocated install.
        fake: Fake secret values.
        capsys: Captures CLI output.
        body: Broken table body.

    Returns:
        None

    Raises:
        None
    """
    number = str(10**29 + secrets.randbelow(10**29))
    short = secrets.token_hex(3)
    table = body.format(s=fake.auth, n=number, short=short)
    install.write(f"{fake.file_text()}\n[credentials.broken]\n{table}")
    seen = _observe(install, capsys)
    assert "credentials.broken" in seen
    assert_no_secret(seen, (*fake.values(), number, short))


@pytest.mark.usefixtures("host_policy")
def test_cred_002_unsafe_permissions(
    install: Install, fake: FakeSecrets, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refusing an unsafe file reports modes and owners only.

    Args:
        install: The relocated install.
        fake: Fake secret values.
        capsys: Captures CLI output.

    Returns:
        None

    Raises:
        None
    """
    install.write(fake.file_text(), 0o666)
    seen = _observe(install, capsys)
    assert "mode 0666" in seen
    assert_no_secret(seen, fake.values())


def _refuse(message: str) -> None:
    """Simulate an SNMP library raising with a message built from its arguments.

    Args:
        message: Exception text.

    Returns:
        None

    Raises:
        ConnectionError: Always.
    """
    raise ConnectionError(message)


def _poll(credential: object) -> None:
    """Simulate SNMP code that fails while a credential is in scope.

    Args:
        credential: The resolved credential.

    Returns:
        None

    Raises:
        RuntimeError: Always, mentioning the credential, chained to a cause.
    """
    try:
        _refuse(f"agent refused {credential!r}")
    except ConnectionError as exc:
        raise RuntimeError(f"poll failed with {credential}") from exc


def test_cred_002_exception_with_credential_in_scope(install: Install, fake: FakeSecrets) -> None:
    """Tracebacks, chained causes, and captured locals show only redacted reprs.

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    install.write(fake.file_text())
    provider = FileCredentialProvider(install.secrets_dir, policy=own_policy())
    v2c = provider.resolve(REF)
    assert isinstance(v2c, SnmpV2cCredential)
    v3 = provider.resolve(CredentialReference("network-snmpv3"))
    dsn = provider.secret("metadata-dsn")
    seen = [failure_text(lambda c=c: _poll(c)) for c in (v2c, v3, dsn)]
    seen.append(failure_text(lambda: v2c.community == fake.community))
    seen.append(failure_text(lambda: Secret(fake.community).missing))  # ty: ignore[unresolved-attribute]
    seen.append(failure_text(lambda: setattr(v3, "username", "x")))
    assert "Secret('***')" in seen[0]
    assert_no_secret("\n".join(seen), fake.values())


class _Capture(logging.Handler):
    """Logging handler that keeps formatted lines and records."""

    def __init__(self) -> None:
        """Create an empty capture.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        super().__init__()
        self.stream = io.StringIO()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Format and keep a record.

        Args:
            record: The record.

        Returns:
            None

        Raises:
            None
        """
        self.records.append(record)
        self.stream.write(self.format(record) + "\n")


def _log_everything(
    logger: logging.Logger, fake: FakeSecrets, provider: FileCredentialProvider
) -> None:
    """Log secrets every careless way: args, mappings, f-strings, extras, exceptions.

    Args:
        logger: Logger under test.
        fake: Fake secret values.
        provider: Loaded provider.

    Returns:
        None

    Raises:
        None
    """
    cred = provider.resolve(REF)
    assert isinstance(cred, SnmpV2cCredential)
    logger.info("community %s", cred.community.reveal())
    logger.info("credential %r and %s", cred, cred)
    logger.info("dsn %(dsn)s", {"dsn": provider.secret("metadata-dsn").reveal()})
    logger.warning(f"auth {fake.auth}")
    logger.info("too many %s %s", fake.privacy)  # noqa: PLE1206 - malformed call under test
    logger.info("extra field", extra={"detail": fake.privacy})
    logger.info(cred.community)
    try:
        _refuse(f"agent rejected {fake.auth}")
    except ConnectionError:
        logger.exception("poll failed for %s", fake.community)
    logger.info("with stack", stack_info=True)


def test_cred_002_logging_filter_redacts_records(install: Install, fake: FakeSecrets) -> None:
    """The filter removes every known secret from messages, args, exceptions, and extras.

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    install.write(fake.file_text())
    provider = FileCredentialProvider(install.secrets_dir, policy=own_policy())
    provider.resolve(REF)
    logger = logging.getLogger("vakr.test.credentials.corpus")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    capture = _Capture()
    capture.addFilter(RedactingFilter(provider.known_secrets))
    logger.addHandler(capture)
    try:
        _log_everything(logger, fake, provider)
    finally:
        logger.removeHandler(capture)
    output = capture.stream.getvalue()
    output += "".join(str(getattr(r, "detail", "")) for r in capture.records)
    assert output.count(MASK) >= 7
    assert "ConnectionError: agent rejected ***" in output
    assert_no_secret(output, fake.values())
    assert all(r.exc_info is None for r in capture.records)


def test_cred_002_filter_passes_records_untouched_without_secrets() -> None:
    """With nothing loaded the filter leaves records alone and never drops them.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "value %s", ("a",), None)
    assert RedactingFilter(lambda: ()).filter(record) is True
    assert record.args == ("a",)


def test_cred_002_repr_str_format_of_every_type(install: Install, fake: FakeSecrets) -> None:
    """``repr``, ``str``, f-strings, ``%``, and ``format`` of every type are redacted.

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    install.write(fake.file_text())
    provider = FileCredentialProvider(install.secrets_dir, policy=own_policy())
    protected = read_protected(install.path, own_policy(), strict=True)
    objects = [
        provider.resolve(REF),
        provider.resolve(CredentialReference("network-snmpv3")),
        provider.secret("metadata-dsn"),
        protected,
        protected.content,
        parse_credentials(protected.content, install.path),
        provider,
        provider.summaries(),
    ]
    rendered = []
    for obj in objects:
        # Every formatting mechanism is exercised on purpose, old styles included.
        rendered += [repr(obj), str(obj), f"{obj}", f"{obj!r}", f"{obj!r:>90}"]
        rendered += ["%s %r" % (obj, obj), "{} {!r}".format(obj, obj)]  # noqa: UP031, UP032
    assert_no_secret("\n".join(rendered), fake.values())


def test_cred_002_json_and_pickle_attempts(install: Install, fake: FakeSecrets) -> None:
    """Serialisers refuse secrets; ``default=str`` yields only redacted text.

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    install.write(fake.file_text())
    provider = FileCredentialProvider(install.secrets_dir, policy=own_policy())
    v2c = provider.resolve(REF)
    assert isinstance(v2c, SnmpV2cCredential)
    v3 = provider.resolve(CredentialReference("network-snmpv3"))
    data = parse_credentials(
        read_protected(install.path, own_policy(), strict=True).content, install.path
    )
    seen = []
    for obj in (v2c, v3, data, provider, v2c.community, dataclasses.asdict(v3)):
        seen.append(failure_text(lambda o=obj: json.dumps(o)))
        seen.append(failure_text(lambda o=obj: pickle.dumps(o)))
    seen.append(json.dumps(dataclasses.asdict(v3), default=str))
    seen.append(json.dumps([dataclasses.asdict(s) for s in provider.summaries()]))
    assert_no_secret("\n".join(seen), fake.values())


@pytest.mark.usefixtures("host_policy")
def test_cred_002_cli_and_health_output_on_a_valid_file(
    install: Install, fake: FakeSecrets, capsys: pytest.CaptureFixture[str]
) -> None:
    """Successful ``check``, ``list``, and health output carry names only.

    Args:
        install: The relocated install.
        fake: Fake secret values.
        capsys: Captures CLI output.

    Returns:
        None

    Raises:
        None
    """
    install.write(fake.file_text())
    assert main(["credentials", "check"]) == 0
    assert main(["credentials", "list"]) == 0
    assert main(["doctor"]) in {0, 1}
    captured = capsys.readouterr()
    seen = "\n".join((captured.out, captured.err, repr(check_credentials())))
    assert "network-snmpv3" in seen
    assert_no_secret(seen, fake.values())


@pytest.mark.parametrize(
    "secret",
    ["quote'and\"double", "back\\slash", "uni-é-\u2603", "with space&amp=;/?"],
)
def test_cred_002_redact_text_covers_escaped_forms(secret: str) -> None:
    """Plain, ``repr``, JSON, and percent-encoded forms of a value are all masked.

    Args:
        secret: A value with characters that change when escaped.

    Returns:
        None

    Raises:
        None
    """
    from urllib.parse import quote

    text = " | ".join((secret, repr({"k": secret}), json.dumps(secret), quote(secret, safe="")))
    redacted = redact_text(text, [Secret(secret)])
    assert secret not in redacted
    assert repr(secret)[1:-1] not in redacted
    assert json.dumps(secret)[1:-1] not in redacted
    assert quote(secret, safe="") not in redacted


def test_cred_002_redact_text_longest_first(fake: FakeSecrets) -> None:
    """A secret containing another is masked whole; plain strings are accepted too.

    Args:
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    longer = f"{fake.community}{fake.auth}"
    redacted = redact_text(f"x {longer} y {fake.community}", [Secret(fake.community), longer, ""])
    assert redacted == f"x {MASK} y {MASK}"


def test_cred_002_traceback_of_rotation_failure_is_clean(
    install: Install, fake: FakeSecrets
) -> None:
    """A reload failure after rotation exposes neither the old nor the new values.

    Args:
        install: The relocated install.
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    install.write(v2c_table("network-v2c", fake.community))
    provider = FileCredentialProvider(install.secrets_dir, policy=own_policy())
    provider.resolve(REF)
    install.write(f'[credentials.network-v2c]\nversion = "2c"\ncommunity = "{fake.auth}\n')
    assert_no_secret(failure_text(lambda: provider.resolve(REF)), fake.values())
    assert provider.known_secrets() == ()
