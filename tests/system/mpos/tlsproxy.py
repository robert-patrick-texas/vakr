"""Test-only TLS for the two-position harness: a test CA, server certificates, and a terminator.

``gen DIR IP...`` writes ``mpos-test-ca.pem`` and, per address, ``<ip>.pem``
and ``<ip>.key``. The certificates meet vakr's strict X.509 profile
(EGRESS-003): a CA with a critical basicConstraints, keyUsage, and subject
key identifier; leaves with the address as subjectAltName, serverAuth, and
an authority key identifier. ``serve CERT KEY HOST PORT TARGET`` terminates
TLS on ``HOST:PORT`` and forwards each connection to ``127.0.0.1:TARGET``
(vakr-api). It stands in for an operator's reverse proxy
(docs/operations/web-access.md); vakr's own TLS checks stay as they are.

Dependencies:
    cryptography (``gen``, on the host); asyncio and ssl only (``serve``,
    in the node, with vakr's bundled Python).

Example:
    $ uv run python tests/system/mpos/tlsproxy.py gen dist/mpos-test/tls 172.29.70.20
"""

import asyncio
import ipaddress
import ssl
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_CHUNK = 65536


def _write(directory: Path, name: str, cert: Any, key: Any) -> None:
    """Write a certificate and its key as PEM.

    Args:
        directory: Target directory.
        name: File stem.
        cert: The certificate.
        key: Its private key.

    Returns:
        None

    Raises:
        OSError: If the files cannot be written.
    """
    from cryptography.hazmat.primitives import serialization

    (directory / f"{name}.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory / f"{name}.key").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))  # fmt: skip


def generate(directory: Path, addresses: list[str]) -> None:
    """Make the test CA and one server certificate per address.

    Args:
        directory: Where to write them.
        addresses: IP addresses, one certificate each.

    Returns:
        None

    Raises:
        OSError: If the files cannot be written.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "vakr mpos test CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False),
                         critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                         critical=False)
          .sign(ca_key, hashes.SHA256()))  # fmt: skip
    _write(directory, "mpos-test-ca", ca, ca_key)
    for address in addresses:
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, address)])
        leaf = (x509.CertificateBuilder().subject_name(name).issuer_name(ca_name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName(
                    [x509.IPAddress(ipaddress.ip_address(address))]), critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                               critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    ca_key.public_key()), critical=False)
                .sign(ca_key, hashes.SHA256()))  # fmt: skip
        _write(directory, address, leaf, key)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy one direction until it ends.

    Args:
        reader: Source.
        writer: Destination.

    Returns:
        None

    Raises:
        None
    """
    try:
        while data := await reader.read(_CHUNK):
            writer.write(data)
            await writer.drain()
    except ConnectionError, OSError:
        pass
    finally:
        writer.close()


async def serve(cert: str, key: str, host: str, port: int, target: int) -> None:
    """Terminate TLS and forward to ``127.0.0.1:target`` until stopped.

    Args:
        cert: Server certificate file.
        key: Its key file.
        host: Address to listen on.
        port: Port to listen on.
        target: Local port of vakr-api.

    Returns:
        None

    Raises:
        OSError: If it cannot listen.
    """
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(cert, key)

    async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        """Connect one client to vakr-api.

        Args:
            client_r: The client's reader.
            client_w: The client's writer.

        Returns:
            None

        Raises:
            None
        """
        try:
            api_r, api_w = await asyncio.open_connection("127.0.0.1", target)
        except OSError:
            client_w.close()
            return
        await asyncio.gather(_pipe(client_r, api_w), _pipe(api_r, client_w))

    server = await asyncio.start_server(handle, host, port, ssl=context)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    if sys.argv[1:2] == ["gen"]:
        generate(Path(sys.argv[2]), sys.argv[3:])
    elif sys.argv[1:2] == ["serve"]:
        cert_file, key_file, listen, port_text, target_text = sys.argv[2:7]
        asyncio.run(serve(cert_file, key_file, listen, int(port_text), int(target_text)))
    else:
        raise SystemExit("usage: tlsproxy.py gen DIR IP... | serve CERT KEY HOST PORT TARGET")
