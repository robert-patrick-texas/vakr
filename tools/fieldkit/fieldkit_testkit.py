# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for the field kit tests: throwaway PKI, local servers, fake commands.

The field kit talks to real infrastructure (GitHub, proxies, SNMP agents,
OpenSSL), so tests stand in for each one locally: a throwaway CA and server
certificate generated with ``openssl`` for every session, a TLS server and
an HTTP CONNECT proxy on 127.0.0.1, and executable stand-ins for
``snmpbulkwalk``, ``ping``, and friends placed first on ``PATH``. Nothing is
committed: every key is generated at test time and discarded (no secrets in
fixtures, CLAUDE.md).

Dependencies:
    pytest: Fixtures.
    openssl: Generates throwaway keys and certificates.
    testing_servers: The loopback TLS server and CONNECT proxy.

Safety:
    Servers bind to 127.0.0.1 on ephemeral ports and stop when the test
    ends. Generated keys live under pytest's temporary directories.

Example:
    $ uv run pytest tools/fieldkit
"""

import ssl
import subprocess
import sys
import tarfile
import threading
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import pytest
from testing_servers import ProxyServer, TlsServer


@dataclass(frozen=True)
class Pki:
    """Throwaway certificates for one test session.

    Attributes:
        ca: CA certificate (stands in for an enterprise inspection CA).
        server_cert: Leaf for ``localhost``/127.0.0.1 signed by ``ca``.
        server_key: Key of ``server_cert``.
        recipient: Self-signed recipient certificate for CMS tests.
        recipient_key: Key of ``recipient``.
        other: A second recipient certificate (wrong-recipient tests).
        other_key: Key of ``other``.
        no_aki_cert: Leaf for ``localhost`` signed by ``ca`` without an
            authority key identifier, as some inspection proxies mint them
            (fails only the strict X.509 profile, EGRESS-T05).
        no_aki_key: Key of ``no_aki_cert``.
    """

    ca: Path
    server_cert: Path
    server_key: Path
    recipient: Path
    recipient_key: Path
    other: Path
    other_key: Path
    no_aki_cert: Path
    no_aki_key: Path


def openssl(*args: str) -> None:
    """Run openssl quietly, failing the test on error.

    Args:
        *args: openssl arguments.

    Returns:
        None

    Raises:
        subprocess.CalledProcessError: If openssl fails.
    """
    subprocess.run(["openssl", *args], check=True, capture_output=True)  # noqa: S603, S607


def _self_signed(directory: Path, name: str, cn: str, ca: bool) -> tuple[Path, Path]:
    """Create a self-signed certificate and key.

    Args:
        directory: Output directory.
        name: File stem.
        cn: Subject common name.
        ca: Whether to mark it as a CA.

    Returns:
        tuple[Path, Path]: Certificate and key paths.

    Raises:
        subprocess.CalledProcessError: If openssl fails.
    """
    cert, key = directory / f"{name}.pem", directory / f"{name}-key.pem"
    constraint = "critical,CA:TRUE" if ca else "critical,CA:FALSE"
    # Python 3.13+ verifies with VERIFY_X509_STRICT, which requires keyUsage on CAs.
    usage = "critical,keyCertSign,cRLSign" if ca else "critical,keyEncipherment"
    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
            "-days", "2", "-subj", f"/O=Fieldkit Tests/CN={cn}",
            "-addext", f"basicConstraints={constraint}",
            "-addext", f"keyUsage={usage}")  # fmt: skip
    return cert, key


@pytest.fixture(scope="session")
def pki(tmp_path_factory: pytest.TempPathFactory) -> Pki:
    """Generate the session's throwaway PKI.

    Args:
        tmp_path_factory: Pytest session temporary directories.

    Returns:
        Pki: Certificate and key paths.

    Raises:
        subprocess.CalledProcessError: If openssl fails.
    """
    d = tmp_path_factory.mktemp("pki")
    ca, ca_key = _self_signed(d, "ca", "Fieldkit Test Inspection CA", ca=True)
    server_cert, server_key = _leaf(d, "server", (ca, ca_key), "")
    no_aki = "authorityKeyIdentifier=none\nsubjectKeyIdentifier=none\n"
    no_aki_cert, no_aki_key = _leaf(d, "no-aki", (ca, ca_key), no_aki)
    recipient, recipient_key = _self_signed(d, "recipient", "test recipient", ca=False)
    other, other_key = _self_signed(d, "other", "other recipient", ca=False)
    return Pki(ca, server_cert, server_key, recipient, recipient_key, other, other_key,
               no_aki_cert, no_aki_key)  # fmt: skip


def _leaf(directory: Path, name: str, issuer: tuple[Path, Path], extra: str) -> tuple[Path, Path]:
    """Issue a ``localhost``/127.0.0.1 server certificate from ``issuer``.

    Args:
        directory: Output directory.
        name: File stem.
        issuer: CA certificate and key paths.
        extra: Additional extension-file lines (``openssl x509 -extfile``).

    Returns:
        tuple[Path, Path]: Certificate and key paths.

    Raises:
        subprocess.CalledProcessError: If openssl fails.
    """
    key, csr, cert = (
        directory / f"{name}-key.pem",
        directory / f"{name}.csr",
        directory / f"{name}.pem",
    )
    openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
            "-out", str(csr), "-subj", "/CN=localhost")  # fmt: skip
    ext = directory / f"{name}-ext.cnf"
    ext.write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\n" + extra, encoding="ascii")
    openssl("x509", "-req", "-in", str(csr), "-CA", str(issuer[0]), "-CAkey", str(issuer[1]),
            "-CAcreateserial", "-days", "2", "-extfile", str(ext),
            "-out", str(cert))  # fmt: skip
    return cert, key


@pytest.fixture
def tls_server(pki: Pki) -> Generator[int]:
    """Run a TLS server for ``localhost`` on an ephemeral port.

    Args:
        pki: Session PKI.

    Returns:
        Generator[int]: The listening port.

    Raises:
        None
    """
    yield from _serve(pki.server_cert, pki.server_key)


@pytest.fixture
def tls_server_no_aki(pki: Pki) -> Generator[int]:
    """Run a TLS server whose leaf lacks an authority key identifier (EGRESS-T05).

    Args:
        pki: Session PKI.

    Returns:
        Generator[int]: The listening port.

    Raises:
        None
    """
    yield from _serve(pki.no_aki_cert, pki.no_aki_key)


def _serve(cert: Path, key: Path) -> Generator[int]:
    """Serve TLS with ``cert`` on an ephemeral loopback port until closed.

    Args:
        cert: Server certificate.
        key: Its private key.

    Returns:
        Generator[int]: The listening port; the server stops afterwards.

    Raises:
        None
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    server = TlsServer(context)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def connect_proxy() -> Generator[tuple[int, list[bytes]]]:
    """Run a CONNECT proxy on an ephemeral port.

    Args:
        None

    Returns:
        Generator[tuple[int, list[bytes]]]: Port and the request heads seen.

    Raises:
        None
    """
    server = ProxyServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], server.seen
    server.shutdown()
    server.server_close()


def fake_command(bin_dir: Path, name: str, body: str) -> Path:
    """Write an executable Python stand-in for an external command.

    Args:
        bin_dir: Directory placed first on ``PATH``.
        name: Command name, such as ``snmpbulkwalk``.
        body: Python source run with ``sys.argv`` as the command saw it.

    Returns:
        Path: The script.

    Raises:
        OSError: If it cannot be written.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / name
    path.write_text(f"#!{sys.executable}\nimport json, os, sys\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def read_bundle(path: Path) -> dict[str, bytes]:
    """Read every regular member of a plaintext bundle into memory.

    Args:
        path: ``.tar.gz`` bundle.

    Returns:
        dict[str, bytes]: Member name to content.

    Raises:
        tarfile.TarError: If the archive is corrupt.
    """
    members: dict[str, bytes] = {}
    with tarfile.open(path, "r:gz") as tar:
        for info in tar:
            stream = tar.extractfile(info)
            if stream is not None:
                members[info.name] = stream.read()
    return members
