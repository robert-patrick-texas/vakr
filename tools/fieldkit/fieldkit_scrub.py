# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Pseudonymize identifying values inside arbitrary command output (FIELD-005).

Host commands (``df``, ``timedatectl``, ``chronyc``, ``ping``, journald
excerpts) print addresses, MACs, and host names in free-form text. The
``Scrubber`` finds them with patterns that refuse to match inside longer
dotted or colon-separated runs, so OIDs, version strings, and timestamps
survive untouched, and replaces them with the run's keyed pseudonyms. It
also remembers every host name the run has seen (the local host, listed
devices, the peer, ``sysName`` values) and replaces those wherever they
appear.

Dependencies:
    fieldkit_pseudo: The keyed mappings.

Safety:
    Free-form text can contain names the scrubber has never seen (for
    example a proxy's domain in a log line); the operator guide says so.
    SNMP free-text columns are handled by ``fieldkit_mibscrub``, which
    replaces every token rather than relying on recognition.

Example:
    >>> from fieldkit_pseudo import Pseudonymizer
    >>> s = Scrubber(Pseudonymizer(bytes(32)))
    >>> s.add_name("rtr1.example.net")
    >>> "rtr1" in s.text("PING rtr1.example.net (10.0.0.1)")
    False
"""

import re

from fieldkit_pseudo import Pseudonymizer, is_ip

_IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\w|\.\d)")
_IPV6_RE = re.compile(r"(?<![\w:.])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![\w:.])")
MAC_RE = re.compile(
    r"(?<![\w:-])[0-9A-Fa-f]{1,2}([:-])(?:[0-9A-Fa-f]{1,2}\1){4}[0-9A-Fa-f]{1,2}(?![\w:-])"
)
_CISCO_MAC_RE = re.compile(r"(?<![\w.])[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}(?![\w.])")
_SERIAL_RE = re.compile(r"\b[A-Z]{3}\d{4}[A-Z0-9]{4}\b")  # Cisco-style FOC1234X5YZ
_CHRONY_REF_RE = re.compile(r"(Reference ID\s*:\s*)([0-9A-Fa-f]{8})\b")


class Scrubber:
    """Generic text pseudonymization plus the run's registry of host names.

    Not thread-safe; one instance per collection run.

    Attributes:
        p: The run's pseudonymizer.
    """

    def __init__(self, pseudo: Pseudonymizer) -> None:
        """Create a scrubber around a pseudonymizer.

        Args:
            pseudo: Keyed mappings for this run.

        Returns:
            None

        Raises:
            None
        """
        self.p = pseudo
        self._names: set[str] = set()
        self._names_re: re.Pattern[str] | None = None

    def add_name(self, name: str) -> None:
        """Register a host name to replace wherever it appears in text.

        Args:
            name: Host name or FQDN; IP literals are ignored because the
                address patterns already cover them.

        Returns:
            None

        Raises:
            None
        """
        name = name.strip().lower()
        if name and not is_ip(name) and name not in self._names:
            self._names.add(name)
            self._names_re = None

    def add_host(self, name: str) -> None:
        """Register a host name and, for an FQDN, its short name too.

        Args:
            name: Host name, FQDN, or IP literal. The first label is only
                registered when it contains a letter and has at least three
                characters, so an address octet or ``a`` never becomes a name.

        Returns:
            None

        Raises:
            None
        """
        self.add_name(name)
        label = name.strip().split(".")[0]
        if not is_ip(name.strip()) and len(label) >= 3 and re.search("[A-Za-z]", label):
            self.add_name(label)

    def _names_pattern(self) -> re.Pattern[str] | None:
        """Return one regex matching every registered name as a whole word.

        Args:
            None

        Returns:
            re.Pattern[str] | None: Case-insensitive pattern with the longest
            name first, so ``rtr1.east`` wins over ``rtr1``; ``None`` if empty.

        Raises:
            None
        """
        if self._names and self._names_re is None:
            alts = "|".join(re.escape(n) for n in sorted(self._names, key=len, reverse=True))
            self._names_re = re.compile(rf"(?<![\w.-])(?:{alts})(?![\w-])", re.IGNORECASE)
        return self._names_re

    def mac_text(self, text: str) -> str:
        """Map a MAC written as ``0:1a:..``, ``00-1A-..``, or ``001a.2b3c.4d5e``.

        Args:
            text: MAC address in one of those notations.

        Returns:
            str: Pseudonymous MAC in the same notation and letter case.

        Raises:
            None
        """
        upper = any(c.isupper() for c in text)
        if "." in text:
            mapped = self.p.mac(bytes.fromhex(text.replace(".", ""))).hex()
            out = ".".join(mapped[i : i + 4] for i in range(0, 12, 4))
            return out.upper() if upper else out
        sep = ":" if ":" in text else "-"
        parts = text.split(sep)
        padded = all(len(part) == 2 for part in parts)
        mapped_bytes = self.p.mac(bytes(int(part, 16) for part in parts))
        out = sep.join(f"{b:02x}" if padded else f"{b:x}" for b in mapped_bytes)
        return out.upper() if upper else out

    def text(self, text: str) -> str:
        """Replace addresses, MACs, serial numbers, and known names in any text.

        Args:
            text: Command output or other semi-structured text.

        Returns:
            str: Text with identifying values pseudonymized.

        Raises:
            None
        """
        p = self.p
        text = MAC_RE.sub(lambda m: self.mac_text(m.group(0)), text)
        text = _CISCO_MAC_RE.sub(lambda m: self.mac_text(m.group(0)), text)
        text = _IPV4_RE.sub(lambda m: p.ip(m.group(0)), text)
        text = _IPV6_RE.sub(lambda m: p.ip(m.group(0)), text)
        text = _SERIAL_RE.sub(lambda m: p.token(m.group(0)), text)
        # chrony prints its NTP reference as hex; it is an IPv4 address in disguise.
        text = _CHRONY_REF_RE.sub(lambda m: m[1] + f"{p.ipv4_int(int(m[2], 16)):08X}", text)
        names = self._names_pattern()
        return names.sub(lambda m: p.hostname(m.group(0)), text) if names else text
