"""Tests for secrets, credential types, the protected file provider, and redaction.

CRED-001 (permission matrix) and CRED-002 (redaction corpus) live here, with
unit tests for SNMPv3 combinations, rotation, the CLI, and the health check.

Dependencies:
    pytest: Test runner; fixtures live in ``conftest.py``, plain helpers in
        ``credentials_testkit.py``.

Example:
    $ uv run pytest tests/unit/credentials
"""
