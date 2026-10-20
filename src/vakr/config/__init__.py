"""vakr configuration: packaged defaults, operator overrides, and validation.

Operators keep only the settings they change in ``/etc/vakr`` (CFGM-001);
everything else comes from the packaged option catalog. Other components read
configuration exclusively through ``vakr.config.loader.load_effective_config``
so precedence, validation, and source reporting stay in one place.

Dependencies:
    tomllib: Reads TOML without third-party packages.

Safety:
    Configuration never contains secrets; credentials live in the separate
    protected provider (SEC-001).

Example:
    >>> from vakr.config.loader import EffectiveConfig
    >>> EffectiveConfig({"node": {"role": "primary"}}).get("node.role")
    'primary'
"""
