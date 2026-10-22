"""Every self-metric family: type, help, and the fixed values of each label (E.33).

Cardinality is bounded by construction: a family declares its label names,
and each label a fixed set of values (``None`` only for ``version``, which
must match a release-number pattern). ``build`` refuses anything else, so
the collector cannot write, and the API cannot answer, a label value that
names a device, an interface, a user, an address, or a path (E.33 #7).
``check`` applies the same rules to a family read back from the
collector's snapshot file. The tables are in ``catalog_api`` and
``catalog_collector``; ``docs/operations/metrics.md`` lists them too, and a
test keeps the page and the tables equal.

Dependencies:
    vakr.metrics.exposition: The model.
    vakr.metrics.catalog_api, catalog_collector: The tables.

Safety:
    A refused label is a programming error (``ValueError``) on the writing
    side and a corrupt snapshot on the reading side; never a new series.

Example:
    >>> fam = build("vakr_api_connections", [(("tcp",), 3)])
    >>> fam.kind.value, fam.samples[0].labels
    ('gauge', (('listener', 'tcp'),))
"""

import re
from collections.abc import Iterable, Sequence

from vakr.logic.window import Quantiles
from vakr.metrics.catalog_api import API_FAMILIES
from vakr.metrics.catalog_collector import COLLECTOR_FAMILIES
from vakr.metrics.catalog_spec import QUANTILES, FamilySpec
from vakr.metrics.exposition import Family, Kind, Sample

__all__ = ["CATALOG", "COLLECTOR_NAMES", "FamilySpec", "build", "check", "summary"]

_VERSION = re.compile(r"[0-9A-Za-z.+~-]{1,64}")
CATALOG: dict[str, FamilySpec] = {s.name: s for s in (*API_FAMILIES, *COLLECTOR_FAMILIES)}
COLLECTOR_NAMES = frozenset(s.name for s in COLLECTOR_FAMILIES)
"""Families only the collector's snapshot may carry."""


def _allowed(spec: FamilySpec, name: str, value: str) -> bool:
    """Tell whether one label value is in its fixed set.

    Args:
        spec: The family.
        name: The label name.
        value: Its value.

    Returns:
        bool: True when allowed.

    Raises:
        None
    """
    values = dict(spec.labels).get(name)
    if values is None:
        return name in dict(spec.labels) and bool(_VERSION.fullmatch(value))
    return value in values


def check(spec: FamilySpec, sample: Sample) -> bool:
    """Tell whether a sample fits its family's declared labels and values.

    Args:
        spec: The family.
        sample: The sample.

    Returns:
        bool: True when the labels, the suffix, and the value are allowed.

    Raises:
        None
    """
    names = tuple(n for n, _ in spec.labels)
    if spec.kind is Kind.SUMMARY and sample.suffix == "":
        names = (*names, "quantile")
    elif sample.suffix and spec.kind is not Kind.SUMMARY:
        return False
    if tuple(n for n, _ in sample.labels) != names:
        return False
    for name, value in sample.labels:
        ok = value in QUANTILES if name == "quantile" else _allowed(spec, name, value)
        if not ok:
            return False
    return not (spec.kind is Kind.COUNTER and sample.value < 0)


def build(name: str, rows: Iterable[tuple[Sequence[str], float | None]]) -> Family:
    """Build a family from label values and values; ``None`` values are left out.

    Args:
        name: A catalogued family.
        rows: Label values in the declared order, and the value.

    Returns:
        Family: The family; its type and help come from the catalog.

    Raises:
        ValueError: If the name is unknown or a label value is not allowed.
    """
    spec = CATALOG[name]
    keys = tuple(n for n, _ in spec.labels)
    samples = []
    for values, value in rows:
        if value is None:
            continue  # unknown is absent, never 0 (invariant 1)
        sample = Sample(tuple(zip(keys, values, strict=False)), float(value))
        if len(values) != len(keys) or not check(spec, sample):
            raise ValueError(f"{name}: label values {tuple(values)!r} are not allowed")
        samples.append(sample)
    return Family(spec.name, spec.kind, spec.help, tuple(samples))


def summary(name: str, rows: Iterable[tuple[Sequence[str], Quantiles | None]]) -> Family:
    """Build a summary family: quantiles, ``_sum``, and ``_count`` per label set.

    Args:
        name: A catalogued summary.
        rows: Label values and the summary's current values (``None``: no sample yet).

    Returns:
        Family: The family.

    Raises:
        ValueError: If the name is unknown, not a summary, or a label is not allowed.
    """
    spec = CATALOG[name]
    if spec.kind is not Kind.SUMMARY:
        raise ValueError(f"{name} is not a summary")
    keys = tuple(n for n, _ in spec.labels)
    samples: list[Sample] = []
    for values, q in rows:
        if q is None:
            continue
        if len(values) != len(keys):
            raise ValueError(f"{name}: label values {tuple(values)!r} are not allowed")
        base = tuple(zip(keys, values, strict=True))
        for quantile, value in zip(QUANTILES, (q.p50, q.p95, q.p99), strict=True):
            samples.append(Sample((*base, ("quantile", quantile)), value))
        samples += [Sample(base, q.total, "_sum"), Sample(base, q.count, "_count")]
    for sample in samples:
        if not check(spec, sample):
            raise ValueError(f"{name}: label values are not allowed")
    return Family(spec.name, spec.kind, spec.help, tuple(samples))
