"""Portable lifecycle tooling: self-update, config upgrade, and egress helpers.

Code here supports installing, updating, and configuring vakr rather than
monitoring networks. Under blueprint section 29 it must not import vakr's
domain, application, or adapter packages, and it takes project-specific values
(repository, package name, paths, signers) as parameters, so another project
can lift it out unchanged. An import-boundary test enforces this.

Dependencies:
    None at import time.

Safety:
    Importing this package performs no I/O.

Example:
    >>> import vakr.lifecycle
"""
