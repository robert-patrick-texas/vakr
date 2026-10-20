# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Register the field kit's shared fixtures with pytest.

The helpers live in ``fieldkit_testkit`` under a unique module name, because
several portable tools sit on the same import path and a bare ``conftest``
import would resolve to whichever tool comes first.

Dependencies:
    fieldkit_testkit: Throwaway PKI, loopback servers, and fake commands.

Example:
    $ uv run pytest tools/fieldkit
"""

from fieldkit_testkit import connect_proxy, pki, tls_server, tls_server_no_aki

__all__ = ["connect_proxy", "pki", "tls_server", "tls_server_no_aki"]
