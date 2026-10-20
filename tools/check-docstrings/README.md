# check-docstrings

A small, portable gate that fails when Python docstrings lack the sections a project requires.

ruff's `D` rules (pydocstyle, Google convention) check how docstrings are laid out. They don't check that a module documents its dependencies, or that a function's `Args` section lists its real parameters. pydoclint does check that, but it can't parse the explicit empty form `Args: None` / `Raises: None`. check-docstrings parses each file with `ast` and reports:

- **Modules:** a missing docstring, or a missing required section (default `Dependencies` and `Example`).
- **Classes:** a missing docstring.
- **Functions:** a missing docstring or required section (default `Args`, `Returns` and `Raises`). This covers async functions, methods, properties, nested functions, and tests.
- **Empty sections:** a required section with no text. An empty section must say `None`.
- **`Args` entries:** entries that don't match the function's parameters (see [the rules below](#args-rules)).
- **Examples:** `>>>` examples, in any docstring, that aren't valid Python.

It is standard library only, needs Python 3.15, and never imports or runs the code it checks.

## Usage

```sh
# Check the given files and directories, reading [tool.check-docstrings] from ./pyproject.toml
uv run python tools/check-docstrings/check_docstrings.py src tests tools

# Use another config file
uv run python tools/check-docstrings/check_docstrings.py --config ci/docstrings.toml src
```

Each problem is one line, `path:line: name: message`, where `name` is `<module>` or a dotted name such as `Class.method` or `outer.inner`. A summary goes to stderr:

```text
src/pkg/rates.py:42: calculate_rate: Args missing: elapsed_seconds
src/pkg/rates.py:42: calculate_rate: missing Raises section
check-docstrings: 31 files checked, 2 problems
```

| Exit status | Meaning |
|---|---|
| 0 | Every docstring passes. |
| 1 | At least one problem was reported. |
| 2 | Usage error: a path doesn't exist, the config file can't be read, or the config table is invalid. |

Directories are searched recursively for `*.py`. Hidden directories (`.git`, `.venv`, ...), `__pycache__`, `_site` and `node_modules` are always skipped. An empty file, such as a bare `__init__.py`, has nothing to document and passes.

## Configuration

All keys are optional. The values below are the defaults:

```toml
[tool.check-docstrings]
module-sections = ["Dependencies", "Example"]    # headers every module docstring needs
function-sections = ["Args", "Returns", "Raises"] # headers every function docstring needs
include-tests = true   # false skips test_*.py, *_test.py, conftest.py and tests/ trees
exclude = []           # globs, e.g. ["generated/**", "legacy.py"]
```

An `exclude` pattern is matched against the path relative to the current directory (`*` stays within one directory, and `**` spans directories) and against the bare file or directory name. Unknown keys and wrongly typed values are errors, so a typo can't silently weaken the gate.

## Rules

A section header is a line such as `Args:` at the docstring's base indentation. The section body is the indented block below it. A header written as `Args: None` on one line counts as a section whose body is `None`. `Examples:` satisfies a required `Example`. In a generator, `Yields:` satisfies a required `Returns`.

### Args rules

- Every parameter is listed exactly once, in any order: positional-only, ordinary, keyword-only, `*args` and `**kwargs`. A leading `self` or `cls` of a method defined in a class body is implicit and must not be listed.
- An entry is `name: description` or `name (type): description`, and the type may nest brackets, as in `name (dict[str, list[int]]):`. Lines indented deeper than the entries continue a description.
- `*args` and `**kwargs` may be written with or without their stars. If stars are written, their count must match the parameter.
- A function without parameters has `Args:` followed by `None` (or `None.`). `Args: None` on a function that has parameters is an error.
- `Returns: None` and `Raises: None` are always allowed. Their content is not checked against the code.

### What is skipped

- `@overload` stubs (`@overload`, `@typing.overload`).
- An `__init__` that has no docstring and whose body only assigns attributes of `self` (`self.x = x`, `self.a, self.b = a, b`, annotated assignments, `pass`). The class docstring documents those attributes. If such an `__init__` has a docstring, the docstring is still checked, so it can't drift from the signature.

## Using it in another project

The tool is self-contained in this directory. To reuse it, copy the whole directory, add a `[tool.check-docstrings]` table if the defaults don't fit, and run it in CI:

```sh
cp -r path/to/this/repo/tools/check-docstrings your-project/tools/
uv run python your-project/tools/check-docstrings/check_docstrings.py your-project/src
```

The directory holds `check_docstrings.py` (the entry point, configuration, file discovery and report) and `check_docstrings_rules.py` (the per-docstring rules), split only to keep each file under 300 lines. It also holds `conftest.py`, which puts this directory on `sys.path` for the tests under pytest's `importlib` import mode, the tests, and this README.

**Portability rule:** check-docstrings must never import anything from the project that hosts it, and must never hard-code anything about that project. Every project-specific value comes from the config table or the command line.

## Development

```sh
uv run pytest tools/check-docstrings
uv run ruff check tools/check-docstrings && uv run ruff format --check tools/check-docstrings
```

## Known limitations

- Only `>>>` examples are checked, and only for syntax; they are not executed. Shell (`$ ...`) examples are not checked.
- `Raises` entries are not compared with the exceptions the code raises, and `Returns` content is not compared with the return annotation.
- Section content other than `Args` entries is not inspected, so a required section must exist but its prose is up to the author and reviewer.
- Class docstrings are only required to exist. Attribute documentation (blueprint section 17.5) is left to review.

## License

Apache License 2.0.
