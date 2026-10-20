"""IFID-T01..T06, IFID-T09, IFID-T10: the versioned corpus produces its approved outcomes.

Every case in ``tests/fixtures/reconcile/corpus-v1.toml`` is reconciled and
compared with its approved outcome per row, its candidates, and its missing
and held interfaces. The same inputs in any order give the same answer
(IFID-006), and the corpus names the rules version it was approved under so
a rule change cannot pass silently.

Dependencies:
    pytest; tests.unit.discovery.reconcile.reconcile_corpus.

Example:
    $ uv run pytest tests/unit/discovery/reconcile/test_reconcile_corpus.py
"""

import random

import pytest

from tests.unit.discovery.reconcile.reconcile_corpus import (
    Case,
    corpus_versions,
    interface_id,
    load_cases,
)
from vakr.discovery.reconcile.match import reconcile
from vakr.discovery.reconcile.model import RULES_VERSION, Match, Reconciliation, Unresolved
from vakr.domain.enums import ReconcileOutcome

CASES = load_cases()


def _run(case: Case, *, shuffle: int | None = None) -> Reconciliation:
    """Reconcile one case, optionally with shuffled inputs.

    Args:
        case: The corpus case.
        shuffle: Seed for shuffling rows and stored interfaces, or ``None``.

    Returns:
        Reconciliation: The result.

    Raises:
        None
    """
    stored, rows = list(case.stored.values()), list(case.rows)
    if shuffle is not None:
        rng = random.Random(shuffle)  # noqa: S311  # reproducible order, not secrets
        rng.shuffle(stored)
        rng.shuffle(rows)
    return reconcile(stored, rows, inventory_complete=case.inventory_complete,
                     addresses_complete=case.addresses_complete)  # fmt: skip


def test_ifid_t10_corpus_is_approved_under_current_rules() -> None:
    """IFID-T10: the corpus records the rules version that approved it.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert corpus_versions() == (1, RULES_VERSION)
    assert len(CASES) >= 18
    outcomes = {e["outcome"] for c in CASES for e in c.expect} | {"missing"}
    assert outcomes == {o.value for o in ReconcileOutcome}


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_ifid_t10_corpus_outcomes(case: Case) -> None:
    """IFID-T01..T06, IFID-T10: each row gets its approved outcome and candidates.

    Args:
        case: The corpus case.

    Returns:
        None

    Raises:
        None
    """
    result = _run(case)
    decided: dict[int, Match | Unresolved] = {
        d.row.if_index: d for d in (*result.matches, *result.unresolved)
    }
    assert sorted(decided) == sorted(e["if_index"] for e in case.expect)
    for expected in case.expect:
        got = decided[expected["if_index"]]
        assert got.outcome.value == expected["outcome"], expected
        if isinstance(got, Match):
            want = expected.get("interface")
            assert (got.interface.id if got.interface else None) == (
                interface_id(case.name, want) if want else None
            )
        else:
            assert got.candidates == tuple(
                sorted((interface_id(case.name, k) for k in expected["candidates"]), key=str)
            )
        assert all(item and item == item.strip() for item in got.evidence)
        if "evidence_count" in expected:
            assert len(got.evidence) == expected["evidence_count"]
    assert {i.id for i in result.missing} == {interface_id(case.name, k) for k in case.missing}
    assert set(result.held) == {interface_id(case.name, k) for k in case.held}


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_ifid_t09_input_order_never_changes_the_outcome(seed: int) -> None:
    """IFID-T09, IFID-006: shuffled inputs give identical decisions.

    Args:
        seed: Shuffle seed.

    Returns:
        None

    Raises:
        None
    """
    for case in CASES:
        assert _run(case, shuffle=seed) == _run(case), case.name


def test_ifid004_unresolved_rows_are_never_merged() -> None:
    """IFID-004, invariant 7: no candidate of an unresolved row is matched or missing.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    for case in CASES:
        result = _run(case)
        matched = {m.interface.id for m in result.matches if m.interface is not None}
        missing = {i.id for i in result.missing}
        assert not matched & set(result.held), case.name
        assert not missing & set(result.held), case.name
        assert len(matched) == len([m for m in result.matches if m.interface is not None])
