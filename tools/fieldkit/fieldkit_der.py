# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Read issuer, subject, and validity from a DER X.509 certificate.

When the GitHub reachability test fails certificate verification, the
development side needs to know *who* signed the certificate the production
host was shown: an enterprise TLS-inspection proxy re-signs traffic with its
own CA (FIELD-010, EGRESS-005). Python's ``ssl`` module only decodes a peer
certificate after successful verification, so this module parses the few
fields needed directly from DER, without third-party packages (FIELD-001).

Dependencies:
    None beyond the standard library.

Safety:
    Parsing is bounds-checked and raises ``ValueError`` on malformed input;
    nothing is evaluated or trusted from the certificate.

Example:
    >>> _decode_oid(bytes([0x55, 0x04, 0x03]))
    '2.5.4.3'
"""

_NAME_KEYS = {
    "2.5.4.3": "CN",
    "2.5.4.5": "serialNumber",
    "2.5.4.6": "C",
    "2.5.4.7": "L",
    "2.5.4.8": "ST",
    "2.5.4.10": "O",
    "2.5.4.11": "OU",
    "1.2.840.113549.1.9.1": "emailAddress",
}
_STRING_CODECS = {0x0C: "utf-8", 0x13: "ascii", 0x14: "latin-1", 0x16: "ascii", 0x1E: "utf-16-be"}
_SEQUENCE, _SET, _OID, _VERSION_TAG = 0x30, 0x31, 0x06, 0xA0


def _tlv(data: bytes, pos: int, end: int) -> tuple[int, int, int]:
    """Read one DER tag-length header.

    Args:
        data: Whole DER buffer.
        pos: Offset of the tag byte.
        end: Offset the element must not cross.

    Returns:
        tuple[int, int, int]: Tag, value start offset, value end offset.

    Raises:
        ValueError: If the header or length is malformed or out of bounds.
    """
    if pos + 2 > end:
        raise ValueError("truncated DER header")
    tag, length = data[pos], data[pos + 1]
    pos += 2
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or count > 4 or pos + count > end:
            raise ValueError("unsupported DER length")
        length = int.from_bytes(data[pos : pos + count])
        pos += count
    if pos + length > end:
        raise ValueError("DER element overruns its container")
    return tag, pos, pos + length


def _children(data: bytes, start: int, end: int) -> list[tuple[int, int, int]]:
    """List the elements inside a constructed DER value.

    Args:
        data: Whole DER buffer.
        start: First byte of the container's value.
        end: One past its last byte.

    Returns:
        list[tuple[int, int, int]]: ``(tag, value_start, value_end)`` items.

    Raises:
        ValueError: If any child is malformed.
    """
    items = []
    while start < end:
        tag, vstart, vend = _tlv(data, start, end)
        items.append((tag, vstart, vend))
        start = vend
    return items


def _decode_oid(raw: bytes) -> str:
    """Decode a DER OBJECT IDENTIFIER value to dotted form.

    Args:
        raw: OID value bytes (without tag and length).

    Returns:
        str: Dotted OID such as ``2.5.4.3``.

    Raises:
        ValueError: If ``raw`` is empty.
    """
    if not raw:
        raise ValueError("empty OID")
    first = raw[0]
    parts = [min(first // 40, 2), first - 40 * min(first // 40, 2)]
    value = 0
    for byte in raw[1:]:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(value)
            value = 0
    return ".".join(str(p) for p in parts)


def _decode_name(data: bytes, start: int, end: int) -> str:
    """Render an X.501 Name as ``CN=..., O=...`` in encoded order.

    Args:
        data: Whole DER buffer.
        start: Start of the Name SEQUENCE value.
        end: End of the Name SEQUENCE value.

    Returns:
        str: Comma-separated attribute list.

    Raises:
        ValueError: If the structure is malformed.
    """
    parts = []
    for rdn_tag, rstart, rend in _children(data, start, end):
        if rdn_tag != _SET:
            raise ValueError("Name element is not a SET")
        for _, astart, aend in _children(data, rstart, rend):
            (oid_tag, ostart, oend), (vtag, vstart, vend) = _children(data, astart, aend)[:2]
            if oid_tag != _OID:
                raise ValueError("attribute type is not an OID")
            oid = _decode_oid(data[ostart:oend])
            codec = _STRING_CODECS.get(vtag, "latin-1")
            value = data[vstart:vend].decode(codec, errors="replace")
            parts.append(f"{_NAME_KEYS.get(oid, oid)}={value}")
    return ", ".join(parts)


def certificate_names(der: bytes) -> dict[str, str]:
    """Return issuer, subject, and validity strings of a DER certificate.

    Args:
        der: Certificate in DER form, as from ``getpeercert(binary_form=True)``.

    Returns:
        dict[str, str]: ``issuer``, ``subject``, ``not_before``, ``not_after``
        (validity as the raw UTCTime/GeneralizedTime text).

    Raises:
        ValueError: If the certificate cannot be parsed.
    """
    tag, start, end = _tlv(der, 0, len(der))
    if tag != _SEQUENCE:
        raise ValueError("not a DER certificate")
    tbs_tag, tstart, tend = _children(der, start, end)[0]
    if tbs_tag != _SEQUENCE:
        raise ValueError("missing tbsCertificate")
    fields = _children(der, tstart, tend)
    if fields and fields[0][0] == _VERSION_TAG:
        fields = fields[1:]
    if len(fields) < 5:
        raise ValueError("tbsCertificate is too short")
    validity = _children(der, fields[3][1], fields[3][2])
    times = [der[s:e].decode("ascii", errors="replace") for _, s, e in validity[:2]]
    return {
        "issuer": _decode_name(der, fields[2][1], fields[2][2]),
        "subject": _decode_name(der, fields[4][1], fields[4][2]),
        "not_before": times[0] if times else "",
        "not_after": times[1] if len(times) > 1 else "",
    }
