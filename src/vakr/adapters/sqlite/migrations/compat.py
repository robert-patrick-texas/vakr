"""Is a migration really additive? A static check of its SQL (E.14).

An ``additive`` migration leaves a database an older release can keep
reading and writing, so it may only add things that release never looks
at, or that cost it nothing to ignore:

- ``CREATE TABLE``, ``CREATE VIEW``, and ``CREATE INDEX``; a ``UNIQUE``
  index only on a table the migration itself creates, because on an
  existing table it adds a constraint older writers do not know;
- ``ALTER TABLE ... ADD COLUMN``, ``NOT NULL`` only with a ``DEFAULT``,
  since an older release inserts rows without the column;
- ``CREATE TRIGGER`` and ``INSERT`` only on tables the migration creates.

Anything else is breaking: ``DROP``, ``RENAME``, ``UPDATE``, ``DELETE``,
``PRAGMA``, triggers or rows on existing tables, and a new partition
template version (an ``INSERT INTO partition_template``), because
``partitions.ensure`` reads a family's template without choosing a
version, so an older release could build partitions from DDL it does not
know. The check reads statements only; it never runs SQL.

Dependencies:
    re: Statement and name matching.

Safety:
    Pure text processing.

Example:
    >>> additive_problems("CREATE TABLE cursor (k INTEGER PRIMARY KEY);")
    []
    >>> additive_problems("DROP TABLE obs_icmp_hot;")[0].split(":")[0]
    'DROP TABLE obs_icmp_hot'
"""

import re

_NAME = r"[\"`\[]?(?P<{0}>[A-Za-z_][A-Za-z0-9_]*)[\"`\]]?"
_IF = r"(?:IF\s+NOT\s+EXISTS\s+)?"
_TABLE = re.compile(rf"CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+{_IF}{_NAME.format('t')}", re.I)
_VIEW = re.compile(rf"CREATE\s+VIEW\s+{_IF}{_NAME.format('v')}", re.I)
_INDEX = re.compile(
    rf"CREATE\s+(?P<unique>UNIQUE\s+)?INDEX\s+{_IF}{_NAME.format('i')}\s+ON\s+{_NAME.format('t')}",
    re.I,
)
_TRIGGER = re.compile(rf"CREATE\s+TRIGGER\s+{_IF}.*?\bON\s+{_NAME.format('t')}", re.I | re.S)
_ADD = re.compile(
    rf"ALTER\s+TABLE\s+{_NAME.format('t')}\s+ADD\s+(?:COLUMN\s+)?(?P<d>.*)", re.I | re.S
)
_INSERT = re.compile(rf"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+{_NAME.format('t')}", re.I)
_ONLY = "only CREATE, ALTER TABLE ... ADD COLUMN, and INSERT into new tables are additive"


def statements(sql: str) -> list[str]:
    """Split SQL into statements, without comments, keeping trigger bodies whole.

    Args:
        sql: A migration's statements.

    Returns:
        list[str]: Each statement on one line, with whitespace collapsed.

    Raises:
        None
    """
    out: list[str] = []
    current: list[str] = []
    in_string = False
    for char in _code(sql) + ";":
        in_string = in_string != (char == "'")
        if char != ";" or in_string:
            current.append(char)
            continue
        text = " ".join("".join(current).split())
        if re.match(r"CREATE\s+TRIGGER\b", text, re.I) and not re.search(r"\bEND$", text, re.I):
            current.append(char)  # a ';' inside the trigger body
            continue
        if text:
            out.append(text)
        current = []
    return out


def _code(sql: str) -> str:
    """Drop ``--`` comments outside string literals.

    Args:
        sql: SQL text.

    Returns:
        str: The text without comments; strings keep their content.

    Raises:
        None
    """
    kept, in_string, i = [], False, 0
    while i < len(sql):
        char = sql[i]
        if char == "'":
            in_string = not in_string
        elif not in_string and sql.startswith("--", i):
            end = sql.find("\n", i)
            i = len(sql) if end < 0 else end
            continue
        kept.append(char)
        i += 1
    return "".join(kept)


def additive_problems(sql: str) -> list[str]:
    """List why a migration's SQL is not additive (E.14); empty when it is.

    Args:
        sql: The migration's statements, as ``Migration.sql`` holds them.

    Returns:
        list[str]: One sentence per offending statement, naming it.

    Raises:
        None
    """
    created: set[str] = set()
    problems: list[str] = []
    for text in statements(sql):
        problem = _judge(text, created)
        if problem is not None:
            problems.append(f"{_head(text)}: {problem}")
    return problems


def _judge(text: str, created: set[str]) -> str | None:
    """Judge one statement, remembering the tables it creates.

    Args:
        text: One statement.
        created: Tables created earlier in the migration; updated here.

    Returns:
        str | None: Why it is not additive, or ``None``.

    Raises:
        None
    """
    if (table := _TABLE.match(text)) is not None:
        created.add(table["t"].lower())
        return None
    problem: str | None = _ONLY
    if _VIEW.match(text) is not None:
        problem = None
    elif (index := _INDEX.match(text)) is not None:
        unique_on_old = index["unique"] and index["t"].lower() not in created
        problem = f"a UNIQUE index on existing table {index['t']}" if unique_on_old else None
    elif (trigger := _TRIGGER.match(text)) is not None:
        old = trigger["t"].lower() not in created
        problem = f"a trigger on existing table {trigger['t']}" if old else None
    elif (add := _ADD.match(text)) is not None:
        definition = add["d"].upper()
        strict = re.search(r"\bNOT\s+NULL\b", definition) and "DEFAULT" not in definition
        problem = "a NOT NULL column without a DEFAULT; older inserts lack it" if strict else None
    elif (insert := _INSERT.match(text)) is not None:
        old = insert["t"].lower() not in created
        problem = f"rows in existing table {insert['t']}" if old else None
    return problem


def _head(text: str) -> str:
    """Name a statement by its first words, for messages.

    Args:
        text: One statement.

    Returns:
        str: At most its first five words.

    Raises:
        None
    """
    return " ".join(text.split()[:5]).rstrip("(")
