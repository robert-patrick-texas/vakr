"""Unit tests for local authentication: passwords, throttling, sessions, CSRF, peer credentials.

Dependencies:
    pytest; argon2-cffi with cheap parameters injected; the memory metadata adapter.

Example:
    $ uv run pytest tests/unit/auth
"""
