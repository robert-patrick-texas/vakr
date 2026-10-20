"""Portable outbound HTTPS: trust store, proxies, bounded client, and diagnosis.

Production monitoring hosts reach the internet (GitHub releases, peer nodes)
either directly or through an enterprise HTTP proxy, and some enterprises
decrypt and re-sign outbound TLS with their own CA. This package builds one
verified client configuration for all of that (EGRESS-001, EGRESS-002) and
explains failures step by step (EGRESS-005). It imports nothing from vakr
outside ``vakr.lifecycle`` (blueprint section 29); callers pass every
project-specific value.

Dependencies:
    Standard library (``ssl``, ``socket``, ``http.client``) for transport;
    ``cryptography`` only in ``certinfo`` for reading certificate fields.

Safety:
    Certificate and hostname verification is always on. The single exception
    is ``peek.peek_presented_certificate``, which reads the certificate a
    server presented after verification already failed and never sends
    application data (EGRESS-004, enforced by a static test).

Example:
    >>> from vakr.lifecycle.egress import proxy
    >>> proxy.resolve_policy("", [], {}).https_proxy is None
    True
"""
