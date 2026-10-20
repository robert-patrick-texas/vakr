r"""``Secret``: a string that cannot leak by accident (SEC-003, blueprint 15.2).

SNMP communities, SNMPv3 passphrases, and database DSNs pass through code that
logs, raises, formats, and serialises values. Wrapping each one in ``Secret``
as soon as it is read makes every accidental path safe by construction:
``repr``, ``str``, and ``format`` all render ``Secret('***')``, ``pickle`` and
``json`` refuse it, and comparing it with a plain string raises. The value
leaves the wrapper only through ``reveal()``, so
``grep -rn '\.reveal()' src`` lists every place a secret is handed onward.

Equality and hashing use an HMAC-SHA-256 digest under a random key created
once per process. Equal secrets hash and compare equal, but a hash says
nothing about a secret's length or content and means nothing outside the
process, so a logged hash cannot be attacked offline. Equality compares the
two fixed-size digests with ``hmac.compare_digest``, so timing reveals
neither where two secrets differ nor how long they are.

Dependencies:
    hmac, secrets: Keyed digests and the per-process key.

Safety:
    Python cannot wipe memory, so a revealed ``str`` lives until it is
    garbage-collected. Reveal at the point of use, pass the result straight
    to the library (``CommunityData(cred.community.reveal())``), and never
    bind it to a variable that outlives the call: tracebacks that capture
    local variables would print it.

Example:
    >>> s = Secret("placeholder")
    >>> s, f"{s}", s == Secret("placeholder")
    (Secret('***'), "Secret('***')", True)
"""

import hmac
import secrets
from typing import Never, SupportsIndex, final

REDACTED = "Secret('***')"
"""What every textual rendering of a ``Secret`` shows."""

_DIGEST_KEY = secrets.token_bytes(32)


@final
class Secret:
    """An immutable secret string whose every rendering is redacted.

    Instances refuse pickling, JSON encoding (they are not ``str``), mutation,
    and comparison with ``str`` or ``bytes``. Copies return the same object.

    Attributes:
        None: The value is reachable only through ``reveal()``.
    """

    __slots__ = ("_value",)
    _value: str

    def __init__(self, value: str) -> None:
        """Wrap a secret value.

        Args:
            value: The secret text.

        Returns:
            None

        Raises:
            TypeError: If ``value`` is not a ``str``; the message names only
                the type.
        """
        if not isinstance(value, str):
            message = f"Secret wraps str, not {type(value).__name__}"
            del value  # keep the raw value out of captured-locals tracebacks
            raise TypeError(message)
        object.__setattr__(self, "_value", value)

    def reveal(self) -> str:
        """Return the secret value; the only way to read it.

        Args:
            None

        Returns:
            str: The plain value. Hand it directly to the library that needs
            it; never log, store, or keep it in a long-lived variable.

        Raises:
            None
        """
        return self._value

    def __repr__(self) -> str:
        """Render the redacted form.

        Args:
            None

        Returns:
            str: ``Secret('***')``.

        Raises:
            None
        """
        return REDACTED

    def __str__(self) -> str:
        """Render the redacted form, so ``%s`` and ``print`` stay safe.

        Args:
            None

        Returns:
            str: ``Secret('***')``.

        Raises:
            None
        """
        return REDACTED

    def __format__(self, format_spec: str) -> str:
        """Render the redacted form whatever format spec is asked for.

        Args:
            format_spec: Ignored, so padding cannot reveal the length.

        Returns:
            str: ``Secret('***')``.

        Raises:
            None
        """
        return REDACTED

    def __eq__(self, other: object) -> bool:
        """Compare two secrets in constant time.

        Args:
            other: Another ``Secret``; other types are not equal.

        Returns:
            bool: Whether both secrets hold the same value.

        Raises:
            TypeError: If ``other`` is ``str`` or ``bytes``, because such a
                comparison almost always means a secret was revealed or a
                literal was hard-coded.
        """
        if isinstance(other, Secret):
            return hmac.compare_digest(self._digest(), other._digest())
        if isinstance(other, str | bytes):
            # The plain value is probably a secret too; drop it so a traceback
            # that captures local variables cannot show it.
            del other
            raise TypeError("compare a Secret only with another Secret")
        return NotImplemented

    def __hash__(self) -> int:
        """Hash the keyed digest, which hides length and content.

        Args:
            None

        Returns:
            int: Hash consistent with ``__eq__`` within this process.

        Raises:
            None
        """
        return hash(self._digest())

    def __setattr__(self, name: str, value: object) -> Never:
        """Refuse mutation.

        Args:
            name: Attribute name.
            value: Ignored.

        Returns:
            None

        Raises:
            AttributeError: Always.
        """
        raise AttributeError("Secret is immutable")

    def __delattr__(self, name: str) -> Never:
        """Refuse attribute deletion.

        Args:
            name: Attribute name.

        Returns:
            None

        Raises:
            AttributeError: Always.
        """
        raise AttributeError("Secret is immutable")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        """Refuse pickling, which would write the value to a byte stream.

        Args:
            protocol: Pickle protocol version.

        Returns:
            None

        Raises:
            TypeError: Always.
        """
        raise TypeError("Secret values cannot be pickled or serialised")

    def __getstate__(self) -> Never:
        """Refuse state export for serialisers that bypass ``__reduce_ex__``.

        Args:
            None

        Returns:
            None

        Raises:
            TypeError: Always.
        """
        raise TypeError("Secret values cannot be pickled or serialised")

    def __copy__(self) -> Secret:
        """Return this object; an immutable value needs no copy.

        Args:
            None

        Returns:
            Secret: ``self``.

        Raises:
            None
        """
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> Secret:
        """Return this object, so ``dataclasses.asdict`` keeps the wrapper.

        Args:
            memo: ``copy.deepcopy`` bookkeeping (unused).

        Returns:
            Secret: ``self``.

        Raises:
            None
        """
        return self

    def _digest(self) -> bytes:
        """Return the keyed digest used for equality and hashing.

        Args:
            None

        Returns:
            bytes: HMAC-SHA-256 of the UTF-8 value under the process key.

        Raises:
            None
        """
        return hmac.digest(_DIGEST_KEY, self._value.encode("utf-8", "surrogatepass"), "sha256")
