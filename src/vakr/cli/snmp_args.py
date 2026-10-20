"""Arguments shared by ``vakr snmp discover`` and ``vakr snmp poll``.

The device is named by address and a *credential name*; a community is never
accepted on the command line, where shell history and ``ps`` would keep it
(SNMP-019, blueprint 15.2). ``--community`` and ``-c`` (the net-snmp habit)
exist only to refuse with that explanation, because argparse would otherwise
echo the value back in an "unrecognized arguments" error.

Dependencies:
    argparse: The parser.
    vakr.domain.enums: Address families.

Safety:
    ``RefuseSecret`` never prints the value it refuses.

Example:
    >>> parser = argparse.ArgumentParser(prog="x")
    >>> common_arguments(parser)
    >>> parser.parse_args(["192.0.2.1", "--credential", "net"]).credential
    'net'
"""

import argparse
from typing import Any

from vakr.domain.enums import AddressFamily


class RefuseSecret(argparse.Action):
    """Refuse a community on the command line without echoing it."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: Any,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        """Stop with a fixed message.

        Args:
            parser: The parser.
            namespace: Unused.
            values: The offending value; never printed.
            option_string: Unused.

        Returns:
            None

        Raises:
            SystemExit: Always, with status 2.
        """
        # The value may be a real community; it must not reach any output.
        del namespace, values, option_string
        parser.error(
            "communities are never accepted on the command line; add the community to "
            "/etc/vakr/secrets/credentials.toml and pass --credential NAME (if you typed a "
            "real community, it is now in your shell history: rotate it)"
        )


def common_arguments(action: argparse.ArgumentParser) -> None:
    """Add the target, credential, and transport options.

    Args:
        action: The ``discover`` or ``poll`` parser.

    Returns:
        None

    Raises:
        None
    """
    action.add_argument("target", metavar="TARGET", help="IP address or DNS name of the device")
    action.add_argument(
        "--credential", required=True, metavar="NAME",
        help="name of an SNMPv2c credential in the credentials file (never the community)",
    )  # fmt: skip
    action.add_argument("--port", type=int, metavar="PORT", help="UDP port (default snmp.port)")
    action.add_argument(
        "--timeout", type=float, metavar="SECONDS", help="seconds per attempt, 1-30"
    )
    action.add_argument("--retries", type=int, metavar="N", help="retries after a timeout, 0-5")
    action.add_argument("--family", choices=[f.value for f in AddressFamily], help="address family")
    action.add_argument("--source", metavar="ADDRESS", help="local address to send from")
    action.add_argument(
        "--dev", action="store_true",
        help="development mode: warn about unsafe credential-file permissions instead of failing",
    )  # fmt: skip
    for flag in ("--community", "-c"):
        action.add_argument(flag, action=RefuseSecret, help=argparse.SUPPRESS)
