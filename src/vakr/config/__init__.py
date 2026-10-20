"""vakr configuration: packaged defaults, operator overrides, and validation.

Operators keep only the settings they change in ``/etc/vakr`` (CFGM-001);
everything else comes from the packaged option catalog in ``vakr.defaults``.
Other components read configuration exclusively through
``vakr.config.loader.load_effective_config`` so precedence, validation, and
source reporting stay in one place. The mechanics (layering, migrations,
upgrade, rendering) live in the portable ``vakr.lifecycle.configmerge``
library; ``vakr.config.project`` binds it to vakr's files and paths.

Dependencies:
    vakr.lifecycle.configmerge: Portable layering and upgrade library.

Safety:
    Configuration never contains secrets; credentials live in the separate
    protected provider (SEC-001). Validation rejects credentials embedded in
    proxy URLs (EGRESS-001).

Example:
    >>> from vakr.config.loader import EffectiveConfig
    >>> EffectiveConfig({"node": {"role": "primary"}}).get("node.role")
    'primary'
"""
