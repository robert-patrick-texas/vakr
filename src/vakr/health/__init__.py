"""Self-health checks shared by ``vakr doctor`` and ``vakr check health``.

A NetOps team watches vakr itself from CheckMK or Nagios (OPS-001) and runs
``vakr doctor`` when something looks wrong. Both commands evaluate the same
check functions so a problem never shows up in one and hides from the other.

Dependencies:
    None beyond the standard library.

Safety:
    Checks only read local state; they never change configuration, contact
    managed network devices, or print secrets.

Example:
    >>> from vakr.health.model import Status
    >>> Status.WARNING.exit_code
    1
"""
