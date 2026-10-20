"""Inventory: the file-managed device list and the collector-facing inventory service.

Operators keep their devices, sites, and interface annotations in
``/etc/vakr/inventory.toml`` under version control. vakr validates the file,
shows a deterministic plan of what would change, and applies it through one
metadata unit of work, marking every record ``file:inventory.toml``
(META-008, META-010, CONF-008). Collectors read devices and poll plans
through ``vakr.inventory.service.InventoryService``, which also stores
reconciled discovery results (DISC-001, DISC-007).

Modules:
    model, parse, parse_fields, files, errors: The file format, its parser,
        bounded reading, and located problem messages.
    guardrails, settings: Network target policy (15.5) and configuration,
        including the ``vakr config validate`` rule.
    snapshot, ownership, validate: Stored records, CONF-008 matching, checks.
    plan, plan_model, plan_interfaces, apply, writes, render: Dry-run diff,
        apply with audit, and operator output.
    claim: The audited interface ownership transfer (E.19).
    planning, service: Poll plans and the collector-facing service.
    catalog, reads: Shared tag/group creation and bounded list reads.

Dependencies:
    vakr.ports.metadata, vakr.policy, vakr.discovery.reconcile.

Safety:
    Device records name credentials only (SEC-001); secret values are never
    read here, only the names the credential provider defines.

Example:
    >>> from vakr.inventory.files import INVENTORY_FILE
    >>> INVENTORY_FILE
    'inventory.toml'
"""
