"""Static rules: EGRESS-T04 (no verification bypass) and the section 29 import boundary.

EGRESS-T04 searches every source file under ``src/`` for settings that
disable certificate or hostname verification. The only permitted
occurrence is inside ``peek_presented_certificate`` in
``vakr/lifecycle/egress/peek.py``, which reads the certificate a server
presented after a verifying handshake already failed and never sends
application data (EGRESS-004). The boundary test keeps ``vakr.lifecycle``
reusable by other projects (blueprint section 29).

Dependencies:
    ast, re: Source scanning.

Example:
    $ uv run pytest tests/unit/lifecycle/test_static_rules.py
"""

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src"
PEEK_FILE = SRC / "vakr/lifecycle/egress/peek.py"
ALLOWED_FUNCTION = "peek_presented_certificate"
BYPASS = re.compile(r"CERT_NONE|check_hostname\s*=\s*False|verify\s*=\s*False")
SENDING_CALLS = {"send", "sendall", "sendto", "write", "makefile", "request"}


def _function_lines(path: Path, name: str) -> range:
    """Return the line range of top-level function ``name`` in ``path``.

    Args:
        path: Python source file.
        name: Function name.

    Returns:
        range: Line numbers of the function, decorators excluded.

    Raises:
        AssertionError: When the function does not exist.
    """
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return range(node.lineno, (node.end_lineno or node.lineno) + 1)
    raise AssertionError(f"{name} not found in {path}")


def test_egress_t04_no_verification_bypass_outside_peek() -> None:
    """EGRESS-T04: verification-off settings appear only in the allowlisted peek function.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    allowed = _function_lines(PEEK_FILE, ALLOWED_FUNCTION)
    hits, allowed_hits = [], 0
    for path in sorted(SRC.rglob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if not BYPASS.search(line):
                continue
            if path == PEEK_FILE and number in allowed:
                allowed_hits += 1
            else:
                hits.append(f"{path.relative_to(SRC)}:{number}: {line.strip()}")
    assert hits == []
    # The allowlist must not go stale: the peek really does turn verification off.
    assert allowed_hits == 2


def test_peek_never_sends_application_data() -> None:
    """EGRESS-004: the peek handshake makes no send, write, or HTTP request calls.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    tree = ast.parse(PEEK_FILE.read_text())
    function = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == ALLOWED_FUNCTION
    )
    called = {
        node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert called.isdisjoint(SENDING_CALLS)
    assert "getpeercert" in called


def test_no_configuration_can_disable_verification() -> None:
    """EGRESS-004: no option or key named like ``--insecure`` or ``no_verify`` exists.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    # String literals shaped like option names or config keys, not prose.
    pattern = re.compile(
        r"[\"'](--)?(insecure|no[-_]verify|skip[-_]verify|tls[-_]verify|verify[-_](ssl|tls))[\"']",
        re.IGNORECASE,
    )
    offenders = [
        str(path.relative_to(SRC)) for path in SRC.rglob("*.py") if pattern.search(path.read_text())
    ]
    assert offenders == []


def test_lifecycle_imports_nothing_else_from_vakr() -> None:
    """Section 29: ``vakr.lifecycle`` imports only standard library, third party, or itself.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    offenders = []
    for path in sorted((SRC / "vakr/lifecycle").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                if (name == "vakr" or name.startswith("vakr.")) and not name.startswith(
                    "vakr.lifecycle"
                ):
                    offenders.append(f"{path.relative_to(SRC)}: {name}")
    assert offenders == []
