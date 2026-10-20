"""Interface identity reconciliation: discovered rows to persistent UUIDs (IFID-001..009, 9.8).

A router reload can renumber every ``ifIndex``; a module swap can rename a
port; IOS XE gives many interfaces the same MAC. vakr keeps one persistent
UUID per logical interface so graphs follow the circuit, not the number
(invariant 6), and never merges two histories on a guess (invariant 7).

Modules:
    evidence: Name keys, strong MACs (IFID-008), unique addresses.
    match, candidates: The conservative MVP rules (E.26); pure and deterministic.
    model: Outcomes (matches, unresolved cases, missing and held interfaces)
        and what a discovery write needs.
    records: Row cleaning, observed-field refresh, new identities, lifecycle, history.
    persist, persist_reviews: Writing one discovery through a metadata unit of work.
    admin: Administrator decisions on identity reviews (IFID-007).

ADR-015's weights and margins wait for IOS XE field captures; until then
only exact matches and unambiguous renumbers merge automatically.

Dependencies:
    vakr.discovery.model, vakr.domain, vakr.logic.lifecycle, vakr.policy.

Safety:
    ``persist`` and ``persist_reviews`` write only inside the caller's unit of
    work; ``admin`` opens one unit of work per decision.

Example:
    >>> from vakr.discovery.reconcile.match import reconcile
    >>> reconcile((), ()).missing
    ()
"""
