"""The two-position system-test harness, MPOS-T01 to MPOS-T04 (blueprint 27.5).

``run.sh`` starts an ``east`` and a ``west`` node and a simulated fleet in
containers, cuts and delays traffic between them, and checks what each node
reports. ``simdata.py`` writes the fleet on the host; ``probe.py`` runs
inside a node with the packaged interpreter and reads what that node knows.
See ``docs/development/two-position-tests.md``.

Dependencies:
    None at package level; see each module.

Example:
    $ make mpos-test MPOS_QUICK=1
"""
