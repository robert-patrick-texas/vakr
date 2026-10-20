"""Report the installed vakr version, which the build derives from ``VERSION``.

Operators compare versions when deciding whether a production collector has
picked up a release, and self-update compares the installed version with the
newest GitHub release. The version string exists in exactly one authored file,
the repository-root ``VERSION`` (VER-001); the wheel build copies it into
package metadata, and this module reads it back from there.

Dependencies:
    importlib.metadata: Reads the version recorded when the package was built,
        so no Python source file needs a hard-coded copy.

Safety:
    A missing installation (for example, running from an unbuilt checkout)
    returns a clearly fake version instead of raising, so diagnostics such as
    ``vakr doctor`` still run.

Example:
    >>> get_version().count(".") >= 2
    True
"""

from importlib.metadata import PackageNotFoundError, version

UNKNOWN_VERSION = "0.0.0+unknown"
"""Version reported when vakr runs without installed package metadata."""


def get_version() -> str:
    """Return the version of the installed vakr distribution.

    The value is the one the package build read from the repository ``VERSION``
    file, normalised by Python packaging rules (for example ``0.2.0-rc.1``
    becomes ``0.2.0rc1``).

    Args:
        None

    Returns:
        str: The installed version, or ``UNKNOWN_VERSION`` when vakr is not
        installed as a distribution.

    Raises:
        None
    """
    try:
        return version("vakr")
    except PackageNotFoundError:
        return UNKNOWN_VERSION
