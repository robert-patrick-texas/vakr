"""Packaged configuration data: the option catalog and config migrations.

``options.toml`` describes every option vakr accepts (CFGM-001) and
``config-migrations.toml`` lists the ordered changes between operator-file
schema versions (CFGM-002). They ship inside the Python package so every
command, test, and build step reads the same data; ``vakr config defaults
--output /opt/vakr/share/defaults`` renders the documented, read-only copy
the ``.deb`` installs for operators.

Dependencies:
    None; this package holds data files only.

Safety:
    The files are package-owned and read-only at runtime; nothing writes here.

Example:
    >>> from importlib.resources import files
    >>> "[catalog]" in files("vakr.defaults").joinpath("options.toml").read_text()
    True
"""
