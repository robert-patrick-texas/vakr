"""Backend-neutral contract suites for every persistence adapter (5.2 rule 6, 19.3).

The ``contract_*`` modules define test classes whose names do not start with
``Test``, so pytest does not collect them where they are defined. Each
adapter directory (``memory/`` now, ``sqlite/`` next) subclasses them in its
own ``test_*.py`` module and supplies two fixtures from its ``conftest.py``:

- ``metadata_uow_factory``: a ``MetadataUnitOfWorkFactory`` over a fresh,
  empty metadata store shared by every unit of work it opens;
- ``telemetry_factory``: a ``TelemetryBackendFactory`` over a fresh, empty
  telemetry store shared by every view it returns.

Dependencies:
    pytest; vakr.ports and vakr.domain only. Suites never import an adapter.

Example:
    $ uv run pytest tests/contract
"""
