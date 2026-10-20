"""Read policy and interface-fact files: TOML, or JSON when the name ends in ``.json``.

``/etc/vakr/policies.toml`` (section 11.1) holds the rules; fact fixtures
feed ``vakr policy explain`` until inventory storage exists. This is the
only module in the package that touches the filesystem, so the engine stays
pure. Every problem is reported with the file name in front of the dotted
path, for example ``policies.toml: interface_policy.rules[0].name: required``.

Dependencies:
    json, tomllib: Parsers from the standard library.

Safety:
    Files are read with a size cap and parsed as data; nothing is executed
    or included. Messages name files and paths, never file contents.

Example:
    >>> MAX_FILE_BYTES
    4194304
"""

import json
import tomllib
from pathlib import Path
from typing import Any

from vakr.policy.errors import PolicyError, PolicyIssue
from vakr.policy.facts import InterfaceFacts
from vakr.policy.facts_parse import parse_facts
from vakr.policy.rules import Policy
from vakr.policy.rules_parse import parse_policy

MAX_FILE_BYTES = 4 * 1024 * 1024
"""Largest policy or facts file read; larger files are refused."""


def read_document(path: Path) -> dict[str, Any]:
    """Read and parse one TOML or JSON document.

    Args:
        path: File to read; ``.json`` selects JSON, anything else TOML.

    Returns:
        dict[str, Any]: The top-level table.

    Raises:
        PolicyError: If the file cannot be read, is too large, or does not
            parse to a table.
    """
    kind = "JSON" if path.suffix.lower() == ".json" else "TOML"
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise PolicyError([PolicyIssue(str(path), f"cannot read: {exc.strerror}")]) from None
    if len(raw) > MAX_FILE_BYTES:
        raise PolicyError([PolicyIssue(str(path), f"larger than {MAX_FILE_BYTES} bytes")])
    try:
        text = raw.decode("utf-8")
        data = json.loads(text) if kind == "JSON" else tomllib.loads(text)
    except ValueError as exc:
        raise PolicyError([PolicyIssue(str(path), f"not valid {kind}: {exc}")]) from None
    if not isinstance(data, dict):
        raise PolicyError([PolicyIssue(str(path), f"{kind} document must be a table")])
    return data


def _in_file(path: Path, error: PolicyError) -> PolicyError:
    """Prefix every issue with the file it came from.

    Args:
        path: The file.
        error: Problems with paths relative to the document.

    Returns:
        PolicyError: The same problems, each starting with the file name.

    Raises:
        None
    """
    return PolicyError([PolicyIssue(f"{path}: {i.path}", i.reason) for i in error.issues])


def load_policy_file(path: Path) -> Policy:
    """Read and validate a policies file.

    Args:
        path: File such as ``/etc/vakr/policies.toml``.

    Returns:
        Policy: The validated rules, with ``source`` set to the path.

    Raises:
        PolicyError: If the file is unreadable or any rule is invalid.
    """
    data = read_document(path)
    try:
        return parse_policy(data, str(path))
    except PolicyError as exc:
        raise _in_file(path, exc) from None


def load_facts_file(path: Path) -> tuple[InterfaceFacts, ...]:
    """Read and validate an interface-facts fixture.

    Args:
        path: TOML or JSON file with ``[[interfaces]]`` entries.

    Returns:
        tuple[InterfaceFacts, ...]: The interfaces, in file order.

    Raises:
        PolicyError: If the file is unreadable or any entry is invalid.
    """
    data = read_document(path)
    try:
        return parse_facts(data)
    except PolicyError as exc:
        raise _in_file(path, exc) from None
