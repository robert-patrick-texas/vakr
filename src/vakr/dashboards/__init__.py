"""Custom dashboards: admin writes and their checks (UI-004, DASH-007, SEC-018).

Admins define dashboards through the API (``vakr.api.routes_dashboards``);
``vakr.query.dashboards`` turns one into a bounded view with current values.

Modules:
    service: ``DashboardService`` (create, update, delete, audited).
    references: Checks that widgets name inventory that exists.

Dependencies:
    vakr.domain, vakr.ports: Records and the metadata unit of work.

Safety:
    No credentials pass through; summaries name dashboards only.

Example:
    >>> from vakr.dashboards import DashboardDraft
    >>> DashboardDraft("NOC", "", 60, ()).refresh_s
    60
"""

from vakr.dashboards.service import DashboardDraft, DashboardService

__all__ = ["DashboardDraft", "DashboardService"]
