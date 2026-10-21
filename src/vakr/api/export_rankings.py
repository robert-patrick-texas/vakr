"""CSV and JSON export of a ranking, as the reader sees it (API-012, DASH-003, DASH-004).

``vakr.query.export`` encodes graphs; a ranking is a table, so it gets its
own columns: rank, the interface and its device, site, provider and
circuit, the score and its parts, the coverage it rests on (DASH-005), and
the window in UTC with the display timezone. A ranking holds at most 100
rows (E.28), far inside the export bounds, so it is always synchronous.

Text cells that a spreadsheet would read as a formula (``=``, ``+``, ``-``,
``@``) are prefixed with ``'``: interface aliases come from devices, and a
device is not a trusted author of spreadsheet formulas.

Dependencies:
    csv, io, json: Encoding.
    vakr.query: Ranking views, ``to_jsonable``, ``safe_name``.

Safety:
    Rankings hold no secrets. The filename uses safe characters only.

Example:
    >>> cell("=1+1"), cell("Gi0/1")
    ("'=1+1", 'Gi0/1')
"""

import csv
import io
import json

from vakr.query import ExportFormat, to_jsonable
from vakr.query.export import ExportFile
from vakr.query.jsonable import iso_utc
from vakr.query.ranking_views import ErrorRow, RankingView, UtilizationRow

COMMON = ("rank", "device", "interface", "alias", "site", "provider", "circuit_id",
          "interface_id", "device_id")  # fmt: skip
UTILIZATION = ("score_pct", "direction", "in_p95_pct", "out_p95_pct", "in_p95_bps",
               "out_p95_bps", "capacity_bps", "capacity_source")  # fmt: skip
ERRORS = ("errors", "in_errors", "out_errors", "per_million")
TAIL = ("samples", "expected_samples", "position", "collector_id", "window_start_utc",
        "window_end_utc", "timezone")  # fmt: skip
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def cell(value: object) -> str:
    """Render one CSV cell; ``None`` is empty, never ``0``.

    Args:
        value: The value.

    Returns:
        str: The cell text, formula-guarded for text.

    Raises:
        None
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return f"'{value}" if value.startswith(_FORMULA) else value
    return repr(value) if isinstance(value, float) else str(value)


def _metrics(row: UtilizationRow | ErrorRow) -> tuple[object, ...]:
    """Return a row's kind-specific columns.

    Args:
        row: A ranking row.

    Returns:
        tuple: The values in ``UTILIZATION`` or ``ERRORS`` order.

    Raises:
        None
    """
    if isinstance(row, UtilizationRow):
        return (row.score_pct, row.direction, row.in_p95_pct, row.out_p95_pct, row.in_p95_bps,
                row.out_p95_bps, row.capacity_bps, row.capacity_source)  # fmt: skip
    return row.errors, row.in_errors, row.out_errors, row.per_million


def export_ranking(
    view: RankingView[UtilizationRow] | RankingView[ErrorRow], fmt: ExportFormat
) -> ExportFile:
    """Encode a ranking as CSV or JSON.

    Args:
        view: The ranking.
        fmt: The encoding.

    Returns:
        ExportFile: Name, media type, and content.

    Raises:
        None
    """
    window = view.window
    stem = f"vakr-top-{view.kind}-{view.window_name}-{iso_utc(window.end)}".replace(":", "")
    if fmt is ExportFormat.JSON:
        body = json.dumps(to_jsonable(view), separators=(",", ":")).encode()
        return ExportFile(f"{stem}.json", "application/json; charset=utf-8", body, len(view.rows))
    header = (*COMMON, *(UTILIZATION if view.kind == "utilization" else ERRORS), *TAIL)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    for row in view.rows:
        ref = row.interface
        values = (row.rank, ref.device, ref.name, ref.alias, ref.site, ref.provider,
                  ref.circuit_id, ref.interface_id, ref.device_id, *_metrics(row),
                  row.coverage.valid, row.coverage.expected, view.position, view.collector_id,
                  iso_utc(window.start), iso_utc(window.end), window.timezone)  # fmt: skip
        writer.writerow([cell(v) for v in values])
    body = out.getvalue().encode()
    return ExportFile(f"{stem}.csv", "text/csv; charset=utf-8", body, len(view.rows))
