"""Two-position replication: the partner client, the pull token, and the metadata pull (ADR-024).

Each node of a pair runs a complete node (MPOS-002). The peer pulls the
primary's metadata (``metadata_pull``); both pull each other's observations
(part B, MPOS-004), through the same ``client.PartnerClient``, which carries
the partner's replication token (``token_file``) over strict HTTPS.

Modules:
    client: Bounded GETs to the partner node, with the bearer token.
    token_file: Reads ``/etc/vakr/secrets/replication.token`` in strict mode.
    status: The node-local record of the newest pull (health, metrics).
    metadata_pull: One peer pull: state, pages, verify, apply.

Example:
    >>> import vakr.replication.client as client
    >>> client.MAX_ANSWER_BYTES
    8388608
"""
