"""Portable GitHub-release self-updater with verification, backup, and rollback.

A production node updates itself from signed GitHub releases: it discovers
the newest release on a channel, downloads the package and its signed
checksum list, verifies both before changing anything, backs up
configuration and databases, installs with the system package manager,
runs post-install hooks through the new binary, and rolls back on failure
(blueprint 28.3). Every project-specific value (repository, package name,
signer identity, paths, hooks) is a parameter, so another project can reuse
this package unchanged (section 29).

Dependencies:
    Standard library only, plus ``vakr.lifecycle.egress`` for HTTPS.
    ``ssh-keygen`` verifies signatures; ``apt-get`` installs (injectable).

Safety:
    Verification failure stops the update before any system change. Every
    external command is an argument list, never a shell string.

Example:
    >>> from vakr.lifecycle.update.versions import Version
    >>> Version.parse("7.2.0rc1").debian()
    '7.2.0~rc.1'
"""
