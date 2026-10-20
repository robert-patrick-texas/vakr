# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
r"""Docstring rules for check-docstrings: sections, Args entries and examples.

This is the rule half of check-docstrings; ``check_docstrings.py`` finds the
files, reads the configuration, walks each syntax tree and reports. Given one
docstring or one function definition, the rules here describe what is wrong:
a missing docstring, a required Google-style section that is absent or empty
(an empty section must say ``None``), ``Args`` entries that disagree with the
real parameters, and ``>>>`` examples that are not valid Python. Like the rest
of the tool it imports nothing from the project that vendors it.

Dependencies:
    ast: Reads definitions and parameters from source without importing it.
    doctest: Extracts ``>>>`` examples exactly as doctest would see them.

Safety:
    Pure functions over syntax trees and strings; examples are compiled to
    an AST for a syntax check and never executed.

Example:
    >>> parse_sections("Summary.\n\nArgs:\n    None")
    {'Args': ['    None']}
    >>> args_problems({"x": 0}, ["    y: Wrong name."])
    ['Args missing: x', 'Args documents unknown parameters: y']
"""

import ast
import doctest
import re

# Google style lets these headers stand in for a required one; "Yields" only
# counts in generators, where it describes what the caller iterates over.
ALTERNATES = {"Example": ("Examples",), "Returns": ("Yields",)}
# A header sits at the docstring's base indentation; "Args: None" on one line
# is accepted as the one-line form of an empty section.
HEADER = re.compile(r"([A-Z][A-Za-z]*(?: [A-Za-z]+)*):(?:[ \t]+(None\.?))?[ \t]*")
# "name:", "*name:" or "name (type):"; the lazy type stops at the first "):".
ENTRY = re.compile(r"(\*{0,2})([A-Za-z_]\w*)(?:\s*\(.*?\))?\s*:")
NONE_BODIES = ("None", "None.")
EXAMPLE_FLAGS = ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
type Function = ast.FunctionDef | ast.AsyncFunctionDef


def parse_sections(doc: str) -> dict[str, list[str]]:
    """Split a cleaned docstring into its Google-style sections.

    Args:
        doc (str): Docstring as returned by ``ast.get_docstring``.

    Returns:
        dict[str, list[str]]: Header name mapped to its body lines with their
        indentation kept. A section ends at the next line that is not
        indented; an inline ``None`` becomes a one-line body.

    Raises:
        None.
    """
    sections: dict[str, list[str]] = {}
    body: list[str] | None = None
    for line in doc.expandtabs().splitlines():
        if not line or line[0].isspace():
            if body is not None:
                body.append(line)
        elif match := HEADER.fullmatch(line):
            body = sections.setdefault(match[1], [])
            if match[2]:
                body.append(f"    {match[2]}")
        else:
            body = None
    return sections


def says_none(body: list[str]) -> bool:
    """Report whether a section body is exactly ``None`` (or ``None.``).

    Args:
        body (list[str]): Section body lines from ``parse_sections``.

    Returns:
        bool: True when the only text in the body is ``None``.

    Raises:
        None.
    """
    return " ".join(line.strip() for line in body if line.strip()) in NONE_BODIES


def parameters(node: Function, method: bool) -> dict[str, int]:
    """List the parameters an ``Args`` section must document.

    Args:
        node (Function): Function or async function definition.
        method (bool): True when defined directly in a class body, so a
            leading ``self`` or ``cls`` is implicit and skipped.

    Returns:
        dict[str, int]: Parameter name mapped to its star count (0 for
        ordinary and keyword-only, 1 for ``*args``, 2 for ``**kwargs``).

    Raises:
        None.
    """
    a = node.args
    positional = [*a.posonlyargs, *a.args]
    if method and positional and positional[0].arg in ("self", "cls"):
        positional = positional[1:]
    found = {p.arg: 0 for p in (*positional, *a.kwonlyargs)}
    found |= {a.vararg.arg: 1} if a.vararg else {}
    return found | ({a.kwarg.arg: 2} if a.kwarg else {})


def args_problems(params: dict[str, int], body: list[str]) -> list[str]:
    """Compare a non-empty ``Args`` section with the real parameters.

    Args:
        params (dict[str, int]): Output of ``parameters``.
        body (list[str]): ``Args`` body lines containing some text.

    Returns:
        list[str]: One message per disagreement; empty when they match.

    Raises:
        None.
    """
    if not params:
        return [] if says_none(body) else ["Args must be None: the function takes no parameters"]
    if says_none(body):
        return [f"Args is None but the function takes {', '.join(params)}"]
    lines = [line for line in body if line.strip()]
    indent = min(len(line) - len(line.lstrip()) for line in lines)
    problems, seen = [], []
    # Lines indented deeper than the first entry continue a description.
    for entry in (line.strip() for line in lines if len(line) - len(line.lstrip()) == indent):
        if not (match := ENTRY.match(entry)):
            problems.append(f"Args entry is not 'name:' or 'name (type):': {entry}")
            continue
        stars, name = match[1], match[2]
        if name in seen:
            problems.append(f"Args documents {name} more than once")
        seen.append(name)
        if stars and name in params and len(stars) != params[name]:
            problems.append(f"Args entry {stars}{name} should be {'*' * params[name]}{name}")
    if missing := [p for p in params if p not in seen]:
        problems.append(f"Args missing: {', '.join(missing)}")
    if extra := [s for s in dict.fromkeys(seen) if s not in params]:
        problems.append(f"Args documents unknown parameters: {', '.join(extra)}")
    return problems


def is_generator(node: Function) -> bool:
    """Report whether a function body yields, ignoring nested scopes.

    Args:
        node (Function): Function or async function definition.

    Returns:
        bool: True when the function itself contains ``yield``.

    Raises:
        None.
    """
    stack: list[ast.AST] = list(node.body)
    while stack:
        item = stack.pop()
        if isinstance(item, ast.Yield | ast.YieldFrom):
            return True
        if not isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            stack.extend(ast.iter_child_nodes(item))
    return False


def section_problems(doc: str, required: tuple[str, ...], generator: bool = False) -> list[str]:
    """Check required sections and ``>>>`` examples in one docstring.

    Args:
        doc (str): Cleaned docstring.
        required (tuple[str, ...]): Header names that must be present.
        generator (bool): True for a generator, where ``Yields`` may
            replace ``Returns``.

    Returns:
        list[str]: Missing-section, empty-section and example messages.

    Raises:
        None.
    """
    sections, problems = parse_sections(doc), []
    for name in required:
        names = [n for n in (name, *ALTERNATES.get(name, ())) if n != "Yields" or generator]
        body = next((sections[n] for n in names if n in sections), None)
        if body is None:
            problems.append(f"missing {name} section")
        elif not any(line.strip() for line in body):
            problems.append(f"{name} section is empty; write None")
    try:
        examples = doctest.DocTestParser().get_examples(doc)
    except ValueError as exc:
        return [*problems, f"malformed example: {exc}"]
    for example in examples:
        try:
            compile(example.source, "<example>", "exec", EXAMPLE_FLAGS, dont_inherit=True)
        except SyntaxError as exc:
            problems.append(
                f"example at docstring line {example.lineno + 1} does not parse: {exc.msg}"
            )
    return problems


def stores_attribute(target: ast.expr, owner: str | None) -> bool:
    """Report whether an assignment target only sets attributes of ``owner``.

    Args:
        target (ast.expr): Assignment target, possibly a tuple or list.
        owner (str | None): Name of the method's first parameter.

    Returns:
        bool: True for ``self.x`` or a tuple/list made only of such targets.

    Raises:
        None.
    """
    if isinstance(target, ast.Tuple | ast.List):
        return all(stores_attribute(t, owner) for t in target.elts)
    return isinstance(target, ast.Attribute) and ast.unparse(target.value) == owner


def assigns_only(node: Function) -> bool:
    """Report whether an ``__init__`` only stores attributes on ``self``.

    Args:
        node (Function): The ``__init__`` definition.

    Returns:
        bool: True when every statement is a docstring, ``pass`` or an
        assignment whose targets are all attributes of the first parameter.

    Raises:
        None.
    """
    first = [*node.args.posonlyargs, *node.args.args][:1]
    owner = first[0].arg if first else None
    for stmt in node.body:
        if isinstance(stmt, ast.Assign):
            simple = all(stores_attribute(t, owner) for t in stmt.targets)
        elif isinstance(stmt, ast.AnnAssign):
            simple = stores_attribute(stmt.target, owner)
        else:
            simple = isinstance(stmt, ast.Pass) or (
                isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
            )
        if not simple:
            return False
    return True


def function_problems(node: Function, method: bool, required: tuple[str, ...]) -> list[str]:
    """Check one function or method docstring.

    Args:
        node (Function): Function or async function definition.
        method (bool): True when defined directly in a class body.
        required (tuple[str, ...]): Section headers every function needs.

    Returns:
        list[str]: Problem messages; empty when the docstring is complete or
        the function is an ``@overload`` stub.

    Raises:
        None.
    """
    if any(ast.unparse(d).rsplit(".", 1)[-1] == "overload" for d in node.decorator_list):
        return []
    doc = ast.get_docstring(node)
    if doc is None:
        # The class docstring documents attributes an __init__ merely stores.
        skip = method and node.name == "__init__" and assigns_only(node)
        return [] if skip else ["missing docstring"]
    problems = section_problems(doc, required, is_generator(node))
    args = parse_sections(doc).get("Args")
    if args and any(line.strip() for line in args):
        problems += args_problems(parameters(node, method), args)
    return problems
