"""Manual inventory edits: devices and interface annotations from the web UI, API, and CLI.

``inventory.toml`` is one way to declare devices (META-010); this package is
the other (27.4: "manual device entry"). Records created here are
API-managed (META-008): a later ``vakr inventory apply`` never overwrites
them, and they never edit a record the file manages. Every change runs
through the same field rules, target guardrails, and credential-name check
as the file (``vakr.inventory.parse``, ``validate``), in one metadata unit
of work with its audit event (SEC-018), and moves the metadata change
counter, so collectors pick it up exactly as they do after an apply (6.2).

Modules:
    fields: Stored records as file tables, and parsing edits with the file's rules.
    checks: Cross-record checks: names, addresses, guardrails, sites, credentials.
    ownership: The refusal of a change to a file-managed record, by field.
    records: What an edit answers, and record lookups.
    devices: ``DeviceEditor``: create, change, retire (META-009).
    annotations: ``AnnotationEditor``: annotate one interface; tag many (META-013).
    bulk: Bounds and refusals of a bulk tag change.

Dependencies:
    vakr.inventory: The file's parser, checks, diff, and catalog helpers.
    vakr.ports.metadata: The metadata unit of work.

Safety:
    A device names its credential only (SEC-001); no secret is read or written.

Example:
    >>> from vakr.inventory.edits.devices import DeviceEditor
    >>> DeviceEditor.__name__
    'DeviceEditor'
"""
