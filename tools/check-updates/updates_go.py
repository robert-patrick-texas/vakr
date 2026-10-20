# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Check Go: the toolchain, each ``go.mod`` language version, and its direct dependencies.

Go enters vakr only for components admitted under blueprint section 24
and for build tooling, but once it does, three things can fall behind: the
installed toolchain, the ``go`` directive of each module (the language
version the module promises to build with), and the module's own
dependencies (TOOLS-001 "Go modules"). The first two are compared with the
latest release on go.dev. For dependencies, Go itself is asked: ``go list
-m -u -json all`` reports, for every required module, the newest version
the module proxy offers. Only direct requirements are reported, matching
the PyPI rows; indirect ones follow from them.

Dependencies:
    json: Decodes the stream of JSON objects that ``go list -json`` prints.
    updates_checks: Shared task model and row helpers.

Safety:
    ``go list`` runs in each module's directory with Go's default
    ``-mod=readonly``, so ``go.mod`` and ``go.sum`` are never rewritten. It
    may fill the user's module cache, which is outside the repository.

Example:
    >>> mods = parse_go_list('{"Path": "example.com/m", "Main": true}'
    ...                      '{"Path": "golang.org/x/sys", "Version": "v0.30.0",'
    ...                      ' "Update": {"Version": "v0.31.0"}}')
    >>> [(m.path, m.version, m.update) for m in mods if not m.main]
    [('golang.org/x/sys', 'v0.30.0', 'v0.31.0')]
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from updates_checks import Context, attempt, command_version, make_row, read_local
from updates_fetch import FetchError
from updates_local import discover
from updates_report import Result
from updates_versions import CURRENT, UNKNOWN, UPDATE

GO_LIST = ["go", "list", "-m", "-u", "-json", "all"]


@dataclass(frozen=True, slots=True)
class GoModule:
    """One module from ``go list -m -u -json all``.

    Attributes:
        path: Module path, such as ``golang.org/x/sys``.
        version: Required version; empty for the main module.
        update: Newer version the proxy offers; empty when current.
        main: True for the module being listed.
        indirect: True for requirements marked ``// indirect``.
    """

    path: str
    version: str
    update: str
    main: bool
    indirect: bool


def go_directive(go_mod: Path) -> str:
    """Return the ``go`` directive of a ``go.mod`` file.

    Args:
        go_mod: Path to ``go.mod``.

    Returns:
        str: The language version, such as ``1.27`` or ``1.27.0``.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If there is no ``go`` directive.
    """
    match = re.search(r"^go\s+(\d\S*)\s*$", go_mod.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise ValueError(f"{go_mod.name} has no go directive")
    return match[1]


def parse_go_list(output: str) -> list[GoModule]:
    """Decode the concatenated JSON objects ``go list -m -u -json`` prints.

    Args:
        output: Standard output of the command.

    Returns:
        list[GoModule]: Modules in the order Go lists them.

    Raises:
        ValueError: If the output is not a stream of JSON objects.
        KeyError: If an object has no ``Path``.
    """
    decoder, text, index = json.JSONDecoder(), output.strip(), 0
    modules = []
    while index < len(text):
        item, index = decoder.raw_decode(text, index)
        index += len(text[index:]) - len(text[index:].lstrip())  # skip separating whitespace
        modules.append(
            GoModule(
                item["Path"],
                item.get("Version", ""),
                (item.get("Update") or {}).get("Version", ""),
                bool(item.get("Main")),
                bool(item.get("Indirect")),
            )
        )
    return modules


def list_modules(ctx: Context, go_mod: Path) -> list[GoModule]:
    """Run ``go list -m -u -json all`` in a module's directory and parse it.

    Args:
        ctx: Task context.
        go_mod: Path to the module's ``go.mod``.

    Returns:
        list[GoModule]: The main module and every requirement.

    Raises:
        FetchError: If Go is missing, times out, or exits with an error.
        ValueError: If the output is not a JSON object stream.
        KeyError: If an object has no ``Path``.
    """
    result = ctx.fetcher.run(GO_LIST, cwd=go_mod.parent)
    if result.returncode != 0:
        reason = (result.stderr.strip().splitlines() or ["no output"])[-1]
        raise FetchError(f"go list failed: {reason}")
    return parse_go_list(result.stdout)


def module_rows(ctx: Context, go_mod: Path) -> list[Result]:
    """Report each direct requirement of one Go module.

    Go's own judgement decides the status: a module with an ``Update`` has
    a newer version on the proxy; without one it is current.

    Args:
        ctx: Task context.
        go_mod: Path to the module's ``go.mod``.

    Returns:
        list[Result]: One row per direct requirement, or one unknown row
        when ``go list`` cannot run or its output cannot be read.

    Raises:
        None
    """
    rel = go_mod.relative_to(ctx.root).as_posix()
    try:
        modules = list_modules(ctx, go_mod)
    except (FetchError, ValueError, KeyError) as exc:
        return [Result(f"Go modules ({rel})", "go", "", "", UNKNOWN, "", str(exc))]
    rows = []
    for module in modules:
        if module.main or module.indirect:
            continue
        latest = module.update or module.version
        state = UPDATE if module.update else CURRENT
        notes = f"https://pkg.go.dev/{module.path}@{latest}"
        rows.append(
            Result(module.path, "go", module.version, latest, state, notes, f"required by {rel}")
        )
    return rows


def check_go(ctx: Context, cfg: Mapping[str, Any]) -> list[Result]:
    """Compare the installed Go, each ``go.mod`` directive, and module requirements.

    Args:
        ctx: Task context.
        cfg: The ``[go]`` table.

    Returns:
        list[Result]: A row for the binary, then per ``go.mod`` a directive
        row and, when ``modules`` is enabled, its requirement rows.

    Raises:
        None
    """
    latest = attempt(ctx.upstream.go)
    rows = []
    if argv := cfg.get("binary"):
        current = read_local(lambda: command_version(ctx, argv))
        rows.append(make_row("Go (installed)", "go", current, latest))
    for path in discover(ctx.root, cfg.get("go_mod", [])):
        rel = path.relative_to(ctx.root).as_posix()
        current = read_local(lambda path=path: go_directive(path))
        # The go directive is a language version, so only the minor series matters.
        rows.append(make_row(f"Go ({rel} go directive)", "go", current, latest, precision=2))
        if cfg.get("modules", True):
            rows += module_rows(ctx, path)
    return rows
