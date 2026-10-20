"""Shared test helpers: throwaway PKI, local HTTPS server, CONNECT proxy, signer.

Every certificate, key, and signing key is generated at test time in a
temporary directory; nothing secret is committed (gitleaks stays clean).
Fixtures defined here are registered by the sibling ``conftest.py``.

Dependencies:
    cryptography: Test CA and server certificates.
    ssh-keygen: Throwaway ed25519 release-signing key (UPD-001 tests).

Example:
    $ uv run pytest tests/unit/lifecycle
"""

import hashlib
import http.server
import ipaddress
import json
import select
import shutil
import socket
import socketserver
import ssl
import subprocess
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from vakr.lifecycle.egress.client import Response
from vakr.lifecycle.egress.errors import EgressError
from vakr.lifecycle.update import journal
from vakr.lifecycle.update.model import Hook, Layout, Toolkit
from vakr.lifecycle.update.process import RunResult, run_command
from vakr.lifecycle.update.sources import Project
from vakr.lifecycle.update.updater import SelfUpdater
from vakr.lifecycle.update.verify import ReleaseTrust
from vakr.lifecycle.update.versions import Version

NOW = datetime.now(UTC)


@dataclass
class Cert:
    """A generated certificate with its key and PEM files.

    Attributes:
        cert: Parsed certificate.
        key: Private key.
        pem: Certificate PEM bytes.
        cert_path: Certificate file.
        key_path: Key file.
    """

    cert: x509.Certificate
    key: ec.EllipticCurvePrivateKey
    pem: bytes
    cert_path: Path
    key_path: Path


def make_cert(
    directory: Path,
    name: str,
    *,
    ca: bool,
    issuer: Cert | None = None,
    days: tuple[int, int] = (-1, 365),
    sans: tuple[str, ...] = (),
) -> Cert:
    """Create a certificate (self-signed unless ``issuer`` is given).

    Args:
        directory: Where to write PEM files.
        name: Common name, also used for file names.
        ca: Whether to set basicConstraints CA:TRUE.
        issuer: Signing CA, or ``None`` for self-signed.
        days: Validity start and end offsets from now, in days.
        sans: DNS names or IP addresses for subjectAltName.

    Returns:
        Cert: The generated certificate.

    Raises:
        None
    """
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer.cert.subject if issuer else subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW + timedelta(days=days[0]))
        .not_valid_after(NOW + timedelta(days=days[1]))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if sans:
        names: list[x509.GeneralName] = []
        for san in sans:
            try:
                names.append(x509.IPAddress(ipaddress.ip_address(san)))
            except ValueError:
                names.append(x509.DNSName(san))
        builder = builder.add_extension(x509.SubjectAlternativeName(names), critical=False)
    if ca:
        usage = x509.KeyUsage(
            digital_signature=True,
            content_commitment=False,
            key_encipherment=False,
            data_encipherment=False,
            key_agreement=False,
            key_cert_sign=True,
            crl_sign=True,
            encipher_only=False,
            decipher_only=False,
        )
        builder = builder.add_extension(usage, critical=True)
        builder = builder.add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
    if issuer is not None:
        builder = builder.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer.key.public_key()),
            critical=False,
        )
    cert = builder.sign(issuer.key if issuer else key, hashes.SHA256())
    pem = cert.public_bytes(serialization.Encoding.PEM)
    cert_path, key_path = directory / f"{name}.pem", directory / f"{name}.key"
    cert_path.write_bytes(pem)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return Cert(cert, key, pem, cert_path, key_path)


@dataclass
class Pki:
    """Test certificates.

    Attributes:
        ca: The "enterprise inspection" CA that signs the server certificate.
        other_ca: An unrelated CA standing in for the OS bundle.
        server: Server certificate for 127.0.0.1 and localhost.
        directory: Where the files live.
    """

    ca: Cert
    other_ca: Cert
    server: Cert
    directory: Path


@pytest.fixture
def pki(tmp_path: Path) -> Pki:
    """Generate a test inspection CA, an unrelated CA, and a server certificate.

    Args:
        tmp_path: Temporary directory.

    Returns:
        Pki: The generated material.

    Raises:
        None
    """
    directory = tmp_path / "pki"
    directory.mkdir()
    ca = make_cert(directory, "Example Inspection CA", ca=True)
    other = make_cert(directory, "Unrelated Root CA", ca=True)
    server = make_cert(directory, "server", ca=False, issuer=ca, sans=("127.0.0.1", "localhost"))
    return Pki(ca, other, server, directory)


@dataclass
class Route:
    """A canned HTTP response.

    Attributes:
        status: HTTP status.
        body: Response body.
        headers: Extra headers.
    """

    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class HttpsServer:
    """A running local HTTPS server.

    Attributes:
        base: ``https://127.0.0.1:<port>``.
        port: Listening port.
        routes: Path (with query) to canned response; tests edit it.
        requests: Paths requested, in order.
    """

    base: str
    port: int
    routes: dict[str, Route]
    requests: list[str]


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves canned routes from the server's ``routes`` dict."""

    def _respond(self, send_body: bool) -> None:
        """Send the canned response for ``self.path``.

        Args:
            send_body: False for HEAD.

        Returns:
            None

        Raises:
            None
        """
        server: Any = self.server
        server.requests.append(self.path)
        route = server.routes.get(self.path, Route(404, b"not found"))
        self.send_response(route.status)
        for name, value in route.headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(route.body)))
        self.end_headers()
        if send_body:
            self.wfile.write(route.body)

    def do_GET(self) -> None:
        """Handle GET.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._respond(True)

    def do_HEAD(self) -> None:
        """Handle HEAD.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self._respond(False)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        """Silence request logging.

        Args:
            format: Unused.
            *args: Unused.

        Returns:
            None

        Raises:
            None
        """


class _QuietServer(http.server.ThreadingHTTPServer):
    """Threading server that does not print tracebacks for rejected handshakes."""

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Ignore per-connection errors (clients reject our test certificate).

        Args:
            request: Unused.
            client_address: Unused.

        Returns:
            None

        Raises:
            None
        """


def start_https_server(cert: Cert) -> tuple[HttpsServer, Callable[[], None]]:
    """Start an HTTPS server on 127.0.0.1 presenting ``cert``.

    Args:
        cert: Server certificate and key.

    Returns:
        tuple: The server handle and a function that stops it.

    Raises:
        None
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert.cert_path, cert.key_path)
    server: Any = _QuietServer(("127.0.0.1", 0), _Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.routes, server.requests = {}, []
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def stop() -> None:
        """Stop the server and close its socket.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        server.shutdown()
        server.server_close()

    return HttpsServer(f"https://127.0.0.1:{port}", port, server.routes, server.requests), stop


@pytest.fixture
def https_server(pki: Pki) -> Iterator[HttpsServer]:
    """Run an HTTPS server presenting the inspection-CA-signed certificate.

    Args:
        pki: Test certificates.

    Returns:
        Iterator[HttpsServer]: The running server; stopped afterwards.

    Raises:
        None
    """
    server, stop = start_https_server(pki.server)
    yield server
    stop()


@dataclass
class Proxy:
    """A running CONNECT proxy.

    Attributes:
        url: ``http://127.0.0.1:<port>``.
        targets: ``host:port`` of each CONNECT request.
        refuse: When true, reply 403 instead of tunnelling.
    """

    url: str
    targets: list[str]
    refuse: bool = False


@pytest.fixture
def connect_proxy() -> Iterator[Proxy]:
    """Run a minimal HTTP CONNECT proxy on 127.0.0.1.

    Args:
        None

    Returns:
        Iterator[Proxy]: The running proxy; stopped afterwards.

    Raises:
        None
    """
    state = Proxy("", [])

    class Tunnel(socketserver.BaseRequestHandler):
        """Handles one CONNECT request and pipes bytes both ways."""

        def handle(self) -> None:
            """Tunnel one connection.

            Args:
                None

            Returns:
                None

            Raises:
                None
            """
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = self.request.recv(1024)
                if not chunk:
                    return
                head += chunk
            target = head.split(b" ")[1].decode()
            state.targets.append(target)
            if state.refuse:
                self.request.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                return
            host, _, port = target.rpartition(":")
            upstream = socket.create_connection((host.strip("[]"), int(port)), timeout=5)
            self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            _pipe(self.request, upstream)

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Tunnel)
    server.daemon_threads = True
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield state
    server.shutdown()
    server.server_close()


def _pipe(a: socket.socket, b: socket.socket) -> None:
    """Copy bytes between two sockets until either closes.

    Args:
        a: Client side.
        b: Upstream side.

    Returns:
        None

    Raises:
        None
    """
    with b:
        while True:
            ready, _, _ = select.select([a, b], [], [], 5)
            if not ready:
                return
            for sock in ready:
                data = sock.recv(65536)
                if not data:
                    return
                (b if sock is a else a).sendall(data)


@dataclass
class Signer:
    """A throwaway release-signing key and its allowed_signers file.

    Attributes:
        key: Private key path.
        allowed: ``allowed_signers`` path trusting the key as ``vakr-release``.
        ssh_keygen: Path of ``ssh-keygen``.
    """

    key: Path
    allowed: Path
    ssh_keygen: str

    def sign(self, sums: Path, namespace: str = "vakr-release") -> Path:
        """Sign ``sums`` the way ``tools/release`` does.

        Args:
            sums: ``SHA256SUMS`` file.
            namespace: Signature namespace.

        Returns:
            Path: The ``.sig`` file next to ``sums``.

        Raises:
            subprocess.CalledProcessError: If signing fails.
        """
        sig = sums.with_name(f"{sums.name}.sig")
        sig.unlink(missing_ok=True)
        argv = [self.ssh_keygen, "-Y", "sign", "-q", "-f", str(self.key), "-n", namespace]
        subprocess.run([*argv, str(sums)], check=True, capture_output=True)  # noqa: S603
        return sig


def new_signer(directory: Path, identity: str = "vakr-release") -> Signer:
    """Generate an ed25519 key and an allowed_signers file in ``directory``.

    Args:
        directory: Where to write the key files.
        identity: Principal named in allowed_signers.

    Returns:
        Signer: The signer.

    Raises:
        None
    """
    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen is None:
        pytest.skip("ssh-keygen is required for release signature tests")
    directory.mkdir(parents=True, exist_ok=True)
    key = directory / "release_key"
    argv = [ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-C", "test-release", "-f", str(key)]
    subprocess.run(argv, check=True, capture_output=True)  # noqa: S603
    public = key.with_suffix(".pub").read_text().strip()
    allowed = directory / "allowed_signers"
    allowed.write_text(f'{identity} namespaces="vakr-release" {public}\n')
    return Signer(key, allowed, ssh_keygen)


@pytest.fixture
def signer(tmp_path: Path) -> Signer:
    """A throwaway release signer.

    Args:
        tmp_path: Temporary directory.

    Returns:
        Signer: The signer.

    Raises:
        None
    """
    return new_signer(tmp_path / "signer")


def build_release(directory: Path, signer: Signer, version_deb: str, payload: bytes) -> Path:
    """Write a signed release (package, SHA256SUMS, signature) into ``directory``.

    Args:
        directory: Output directory.
        signer: Release signer.
        version_deb: Debian version string, e.g. ``0.3.0`` or ``0.3.0~rc.1``.
        payload: Fake package bytes.

    Returns:
        Path: The package path.

    Raises:
        None
    """
    directory.mkdir(parents=True, exist_ok=True)
    package = directory / f"vakr_{version_deb}_amd64.deb"
    package.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (directory / "SHA256SUMS").write_text(f"{digest}  {package.name}\n")
    signer.sign(directory / "SHA256SUMS")
    return package


def release_json(
    tag: str, assets: dict[str, str], *, prerelease: bool = False, body: str = ""
) -> dict[str, Any]:
    """Build one GitHub releases API object.

    Args:
        tag: Tag name.
        assets: Asset name to download URL.
        prerelease: GitHub pre-release flag.
        body: Release notes.

    Returns:
        dict[str, Any]: API object.

    Raises:
        None
    """
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": prerelease,
        "body": body,
        "assets": [{"name": n, "browser_download_url": u} for n, u in assets.items()],
    }


def as_json(data: object) -> bytes:
    """Encode test API data.

    Args:
        data: JSON-serializable data.

    Returns:
        bytes: UTF-8 JSON.

    Raises:
        None
    """
    return json.dumps(data).encode()


@pytest.fixture(autouse=True)
def _no_host_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from writing events into the host's journald.

    Args:
        tmp_path: Temporary directory.
        monkeypatch: Pytest monkeypatch.

    Returns:
        None

    Raises:
        None
    """
    monkeypatch.setattr(journal, "JOURNAL_SOCKET", tmp_path / "no-journal.socket")


class FakeHttp:
    """In-memory stand-in for ``HttpsClient`` (``get`` and ``download``).

    Attributes:
        responses: URL to API response for ``get``.
        files: URL to bytes for ``download``.
        log: ``(method, url, headers)`` for each call.
    """

    def __init__(self) -> None:
        """Start with no canned data.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.responses: dict[str, Response] = {}
        self.files: dict[str, bytes] = {}
        self.log: list[tuple[str, str, dict[str, str]]] = []

    def get(self, url: str, headers: Mapping[str, str] | None = None) -> Response:
        """Return the canned response for ``url``, or 404.

        Args:
            url: Requested URL.
            headers: Request headers (recorded).

        Returns:
            Response: Canned response.

        Raises:
            None
        """
        self.log.append(("GET", url, dict(headers or {})))
        return self.responses.get(url, Response(url, 404, {}, b""))

    def download(
        self, url: str, dest: Path, max_bytes: int, headers: Mapping[str, str] | None = None
    ) -> int:
        """Write the canned bytes for ``url`` to ``dest``.

        Args:
            url: Requested URL.
            dest: Destination file.
            max_bytes: Size limit, enforced like the real client.
            headers: Request headers (recorded).

        Returns:
            int: Bytes written.

        Raises:
            EgressError: For unknown URLs or oversize files.
        """
        self.log.append(("DOWNLOAD", url, dict(headers or {})))
        if url not in self.files:
            raise EgressError("http", f"HTTP 404 downloading {url}")
        data = self.files[url]
        if len(data) > max_bytes:
            raise EgressError("http", "too large")
        dest.write_bytes(data)
        return len(data)

    def publish(self, api: str, repo: str, releases: list[dict[str, Any]]) -> None:
        """Serve ``releases`` from the list and per-tag endpoints.

        Args:
            api: API base URL.
            repo: ``owner/name``.
            releases: API release objects.

        Returns:
            None

        Raises:
            None
        """
        url = f"{api}/repos/{repo}/releases?per_page=30"
        self.responses[url] = Response(url, 200, {}, as_json(releases))
        for item in releases:
            tag_url = f"{api}/repos/{repo}/releases/tags/{item['tag_name']}"
            self.responses[tag_url] = Response(tag_url, 200, {}, as_json(item))

    def downloads(self) -> list[str]:
        """Return downloaded URLs in order.

        Args:
            None

        Returns:
            list[str]: URLs passed to ``download``.

        Raises:
            None
        """
        return [url for method, url, _ in self.log if method == "DOWNLOAD"]


class FakeInstaller:
    """Records package installs instead of running ``apt-get``.

    Attributes:
        installed: Package file names, in install order.
        fail_for: Package names whose install fails.
        on_install: Called with each package, to simulate side effects.
    """

    def __init__(self) -> None:
        """Start with no installs.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.installed: list[str] = []
        self.fail_for: set[str] = set()
        self.on_install: Callable[[Path], None] | None = None

    def __call__(self, package: Path) -> RunResult:
        """Pretend to install ``package``.

        Args:
            package: Package file.

        Returns:
            RunResult: Exit 0, or 100 for packages in ``fail_for``.

        Raises:
            None
        """
        self.installed.append(package.name)
        if self.on_install is not None:
            self.on_install(package)
        if package.name in self.fail_for:
            return RunResult(100, "", "E: Sub-process /usr/bin/dpkg returned an error code (1)")
        return RunResult(0)


class HookRunner:
    """Runs ``ssh-keygen`` for real and fakes every other command.

    Attributes:
        calls: Faked commands as ``argv[1:]`` joined by spaces.
        codes: Exit status per faked command (default 0).
    """

    def __init__(self) -> None:
        """Start with no calls.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        self.calls: list[str] = []
        self.codes: dict[str, int] = {}

    def __call__(
        self,
        argv: Sequence[str],
        *,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 900.0,
    ) -> RunResult:
        """Run or fake ``argv``.

        Args:
            argv: Command.
            stdin: Standard input.
            env: Extra environment.
            timeout: Timeout.

        Returns:
            RunResult: Real result for ``ssh-keygen``, faked otherwise.

        Raises:
            None
        """
        if Path(argv[0]).name == "ssh-keygen":
            return run_command(argv, stdin=stdin, env=env, timeout=timeout)
        key = " ".join(argv[1:])
        self.calls.append(key)
        code = self.codes.get(key, 0)
        return RunResult(code, "", f"{key}: exit {code}" if code else "")


@dataclass
class Harness:
    """A complete, relocated updater with fakes for every side effect.

    Attributes:
        root: Temporary root.
        signer: Release signer.
        http: Fake HTTPS client serving releases.
        installer: Fake installer.
        runner: Fake hook runner.
        events: Emitted ``(event_id, message)`` pairs.
        kit: Toolkit wired to the fakes.
        project: Project parameters.
        updater: The updater under test.
        releases: Published API objects, newest first.
    """

    root: Path
    signer: Signer
    http: FakeHttp
    installer: FakeInstaller
    runner: HookRunner
    events: list[tuple[str, str]]
    kit: Toolkit
    project: Project
    updater: SelfUpdater
    releases: list[dict[str, Any]] = field(default_factory=list)

    def publish(self, semver: str, body: str = "", prerelease: bool = False) -> Path:
        """Build, sign, and serve a release.

        Args:
            semver: Version such as ``0.3.0`` or ``0.3.0-rc.1``.
            body: Release notes.
            prerelease: GitHub pre-release flag.

        Returns:
            Path: Local copy of the package.

        Raises:
            None
        """
        version = Version.parse(semver)
        directory = self.root / "published" / semver
        package = build_release(directory, self.signer, version.debian(), f"pkg {semver}".encode())
        assets = {}
        for path in (package, directory / "SHA256SUMS", directory / "SHA256SUMS.sig"):
            url = f"https://dl.example/{version.tag()}/{path.name}"
            self.http.files[url] = path.read_bytes()
            assets[path.name] = url
        item = release_json(version.tag(), assets, prerelease=prerelease, body=body)
        self.releases.insert(0, item)
        self.http.publish("https://api.example", "o/r", self.releases)
        return package

    def asset_url(self, semver: str, name: str) -> str:
        """Return the download URL of one published asset.

        Args:
            semver: Release version.
            name: Asset name.

        Returns:
            str: URL.

        Raises:
            None
        """
        return f"https://dl.example/{Version.parse(semver).tag()}/{name}"

    def ids(self) -> list[str]:
        """Return emitted event IDs.

        Args:
            None

        Returns:
            list[str]: IDs in order.

        Raises:
            None
        """
        return [event_id for event_id, _ in self.events]


def make_harness(root: Path, signer: Signer, http: FakeHttp | None) -> Harness:
    """Wire an updater under ``root`` to fakes.

    Args:
        root: Temporary root.
        signer: Release signer.
        http: Fake client, or ``None`` for an offline host.

    Returns:
        Harness: Ready to use.

    Raises:
        None
    """
    layout = Layout(
        cache_root=root / "cache/updates",
        backups_dir=root / "state/backups",
        state_file=root / "state/update-state.json",
        lock_file=root / "run/self-update.lock",
        config_dir=root / "etc",
        db_dir=root / "state",
    )
    events: list[tuple[str, str]] = []

    def emit(event_id: str, message: str, priority: int = 6) -> None:
        """Record an event.

        Args:
            event_id: Event ID.
            message: Message.
            priority: Ignored.

        Returns:
            None

        Raises:
            None
        """
        del priority
        events.append((event_id, message))

    restart = Hook("restart services", ("/usr/bin/systemctl", "try-restart", "vakr.target"))
    hooks = (
        Hook("config upgrade", ("/new/vakr", "config", "upgrade"), skip_codes=frozenset({3})),
        restart,
        Hook("health check", ("/new/vakr", "check", "health"), ok_codes=frozenset({0, 1, 3})),
    )
    installer, runner = FakeInstaller(), HookRunner()
    kit = Toolkit(layout, hooks, (restart,), installer, runner, emit, lambda: datetime.now(UTC))
    trust = ReleaseTrust(signer.allowed, "vakr-release", "vakr-release", signer.ssh_keygen)
    project = Project("o/r", "vakr", "amd64", trust, api_base="https://api.example")
    fake = http if http is not None else FakeHttp()
    updater = SelfUpdater(project, kit, http)
    return Harness(root, signer, fake, installer, runner, events, kit, project, updater)


@pytest.fixture
def harness(tmp_path: Path, signer: Signer) -> Harness:
    """An online updater harness.

    Args:
        tmp_path: Temporary directory.
        signer: Release signer.

    Returns:
        Harness: Ready to use.

    Raises:
        None
    """
    return make_harness(tmp_path / "host", signer, FakeHttp())
