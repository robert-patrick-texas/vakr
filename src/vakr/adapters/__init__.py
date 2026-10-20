"""Adapters: concrete implementations of the ports in ``vakr.ports`` (blueprint 6.5).

Each subpackage implements one or more ports for one technology. Adapters
are the only place database drivers, SQL, and network libraries may appear
(ARCH-001, 5.2 rules 1-2); everything they return is a domain object.

- ``vakr.adapters.memory``: the in-memory reference adapter. It implements
  every persistence port simply and correctly, proves the contract suites
  are runnable, and serves as the test double for application code.

Dependencies:
    vakr.ports, vakr.domain.

Safety:
    Adapters translate driver errors into ``vakr.domain.errors`` so no SQL
    text or connection detail reaches an operator-facing message.

Example:
    >>> from vakr.adapters.memory import memory_uow_factory
    >>> memory_uow_factory()().__class__.__name__
    'MemoryUnitOfWork'
"""
