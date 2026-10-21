"""Names of the per-request values vakr-api keeps in the ASGI scope.

The middleware, the Unix-socket protocol, and the handlers pass three facts
along with each request: its ID, the client as seen after trusted-proxy
handling, and, for the local socket only, the connecting process's
credentials. They travel as scope keys so that no request header can ever
set them: a TCP client controls its headers, never its scope.

Dependencies:
    None.

Safety:
    ``PEER_KEY`` is set only by the Unix-socket protocol
    (``vakr.api.peer``), never from anything the client sends.

Example:
    >>> PEER_KEY
    'vakr.peer'
"""

REQUEST_ID_KEY = "vakr.request_id"
"""The request ID (``X-Request-ID``)."""
CLIENT_KEY = "vakr.client"
"""The ``ClientView`` after trusted-proxy handling (SEC-013)."""
PEER_KEY = "vakr.peer"
"""The ``SocketPeer`` of a Unix-socket connection (E.12)."""
