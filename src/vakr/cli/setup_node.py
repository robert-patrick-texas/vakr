"""``vakr setup`` node settings: role, position, and the primary's URL (MPOS-001, MPOS-003).

``sudo vakr setup --role peer --position west --primary-url https://...``
writes the ``[node]`` table of ``/etc/vakr/config.toml`` before anything
else, as root, keeping the file's comments, owner, and mode. Without the
flags, setup asks for them at a terminal when ``config.toml`` exists and
sets no ``[node]`` table yet (a first install); otherwise it changes
nothing. The result is checked with ``vakr.config.node.node_problems``
before it is written, so setup never leaves a configuration the services
refuse at start.

Dependencies:
    tomlkit, vakr.lifecycle.configmerge.tree: The comment-preserving edit.
    vakr.config.node: The rules.

Safety:
    Written through a temporary file in the same directory and renamed, with
    the old file's mode and owner; a failed check writes nothing.

Example:
    $ sudo vakr setup --role peer --position west --primary-url https://vakr-east.example.net
"""

import argparse
import os
import sys
from pathlib import Path

import tomlkit

from vakr.config.loader import EffectiveConfig
from vakr.config.node import PEER, PRIMARY, NodeSettings, node_problems, node_settings
from vakr.lifecycle.configmerge.tree import ensure_table


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``--role``, ``--position``, and ``--primary-url``.

    Args:
        parser: The ``setup`` parser.

    Returns:
        None

    Raises:
        None
    """
    parser.add_argument("--role", choices=(PRIMARY, PEER), help="primary (default) or peer")
    parser.add_argument("--position", help="this node's vantage point, such as east")
    parser.add_argument("--primary-url", help="the primary's https:// address (a peer only)")


def _ask(question: str, default: str) -> str:
    """Ask one question at the terminal.

    Args:
        question: The prompt.
        default: Answer for an empty line.

    Returns:
        str: The answer.

    Raises:
        EOFError: When input ends.
    """
    answer = input(f"{question} [{default}]: " if default else f"{question}: ").strip()
    return answer or default


def _wanted(args: argparse.Namespace, doc: tomlkit.TOMLDocument | None) -> dict[str, str]:
    """Collect the node settings to write: the flags, else answers at a first-install terminal.

    Args:
        args: Parsed ``setup`` arguments.
        doc: ``config.toml``, or ``None`` when it does not exist.

    Returns:
        dict[str, str]: ``[node]`` keys to set; empty to change nothing.

    Raises:
        EOFError: When input ends during a question.
    """
    given = {k: v for k, v in (("role", args.role), ("position", args.position),
                                ("primary_url", args.primary_url)) if v}  # fmt: skip
    if given or doc is None or "node" in doc or not (sys.stdin and sys.stdin.isatty()):
        return given
    print("This node's place in a two-position pair (Enter keeps a single node):")
    role = _ask("Role, primary or peer", PRIMARY)
    wanted = {"role": role, "position": _ask("Position label, such as east", "")}
    if role == PEER:
        wanted["primary_url"] = _ask("The primary's https:// address", "")
    return {k: v for k, v in wanted.items() if v}


def apply_node_settings(args: argparse.Namespace, config_file: Path) -> NodeSettings | None:
    """Write the requested ``[node]`` settings, checked; return the result.

    Args:
        args: Parsed ``setup`` arguments.
        config_file: ``/etc/vakr/config.toml``.

    Returns:
        NodeSettings | None: The node settings now in force, or ``None`` when
        nothing was asked for.

    Raises:
        ValueError: If the settings break ``node_problems``, or the file cannot be written.
    """
    text = config_file.read_text(encoding="utf-8") if config_file.exists() else None
    doc = tomlkit.parse(text) if text is not None else None
    wanted = _wanted(args, doc)
    if not wanted:
        return None
    doc = doc if doc is not None else tomlkit.document()
    table = ensure_table(doc, "node")
    for key, value in wanted.items():
        table[key] = value
    merged = EffectiveConfig({"node": dict(doc.unwrap().get("node", {}))})
    if problems := node_problems(merged):
        raise ValueError("; ".join(problems))
    _write(config_file, tomlkit.dumps(doc))
    return node_settings(merged)


def _write(path: Path, text: str) -> None:
    """Replace a file atomically, keeping its mode and owner.

    Args:
        path: The file.
        text: The new content.

    Returns:
        None

    Raises:
        ValueError: If it cannot be written (run setup with sudo).
    """
    part = path.with_name(f".{path.name}.setup")
    try:
        old = path.stat() if path.exists() else None
        part.write_text(text, encoding="utf-8")
        part.chmod(old.st_mode & 0o7777 if old else 0o640)
        if old is not None and os.geteuid() == 0:
            os.chown(part, old.st_uid, old.st_gid)
        part.replace(path)
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise ValueError(f"cannot write {path}: {exc.strerror}; run `sudo vakr setup`") from None
