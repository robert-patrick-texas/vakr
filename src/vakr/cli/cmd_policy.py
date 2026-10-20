"""``vakr policy``: explain and validate collection policies (POLICY-004, POLICY-006).

NetOps asks "why is this interface (not) graphed?" and "will this rule do
what I think before I reload?". ``vakr policy explain`` answers the first
for interfaces described in a facts file, naming every rule considered and
which source decided each value (CFG-009). Explaining interfaces straight
from inventory arrives with M3/M4. ``vakr policy validate`` checks a
policies file and the configured description keywords (CFG-005).

Dependencies:
    vakr.policy: Engine, file loading, and rendering.
    vakr.config.loader: Description keywords from the effective configuration.

Safety:
    Read-only: nothing is written and no device is contacted. Exit status
    is 0 on success, 1 on validation errors, 2 on usage errors.

Example:
    $ vakr policy explain --facts edge-rtr1.toml --policies /etc/vakr/policies.toml
    interface edge-rtr1 GigabitEthernet0/0/0 (ifIndex 1)
      decision: monitored [default]
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from vakr.cli.common import SubParsers
from vakr.config.loader import ConfigError, load_effective_config
from vakr.paths import InstallPaths
from vakr.policy.engine import explain
from vakr.policy.errors import PolicyError
from vakr.policy.files import load_facts_file, load_policy_file
from vakr.policy.keywords import KeywordSet, keywords_from_config
from vakr.policy.render import render_text, to_dict
from vakr.policy.rules import Policy
from vakr.policy.scope import unapplied_settings

POLICIES_FILE = "policies.toml"
"""Policies file inside the configuration directory (section 11.1)."""


def register(subparsers: SubParsers) -> None:
    """Add the ``policy`` command with ``explain`` and ``validate``.

    Args:
        subparsers: The top-level sub-parser collection.

    Returns:
        None

    Raises:
        None
    """
    parser = subparsers.add_parser("policy", help="explain and validate collection policies")
    parser.set_defaults(func=lambda _args: _usage(parser))
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="configuration directory for keywords (default: /etc/vakr, relocated by VAKR_ROOT)",
    )
    actions = parser.add_subparsers(title="subcommands", metavar="SUBCOMMAND")
    show = actions.add_parser("explain", parents=[common], help="why interfaces are monitored")
    show.add_argument("--facts", type=Path, required=True, metavar="FILE", help="interface facts")
    show.add_argument(
        "--policies",
        type=Path,
        metavar="FILE",
        help=f"policies file (default: {POLICIES_FILE} in the configuration directory, if any)",
    )
    show.add_argument("--json", action="store_true", help="print JSON instead of text")
    show.set_defaults(func=run_explain)
    check = actions.add_parser("validate", parents=[common], help="check a policies file")
    check.add_argument("file", type=Path, metavar="FILE", help="policies file to check")
    check.set_defaults(func=run_validate)


def _usage(parser: argparse.ArgumentParser) -> int:
    """Print ``vakr policy`` help when no subcommand is given.

    Args:
        parser: The ``policy`` parser.

    Returns:
        int: 2, the usage-error status.

    Raises:
        None
    """
    parser.print_help(sys.stderr)
    return 2


def _config_dir(args: argparse.Namespace) -> Path:
    """Return the configuration directory from ``--config-dir`` or the install layout.

    Args:
        args: Parsed arguments with ``config_dir``.

    Returns:
        Path: The directory.

    Raises:
        None
    """
    return args.config_dir or InstallPaths.from_environment().config_dir


def _keywords(args: argparse.Namespace) -> KeywordSet:
    """Load the effective description keywords.

    Args:
        args: Parsed arguments with ``config_dir``.

    Returns:
        KeywordSet: Packaged keywords plus the operator's, or only theirs.

    Raises:
        ConfigError: If the configuration is invalid.
        PolicyError: If a keyword is invalid or unsafe.
    """
    return keywords_from_config(load_effective_config(_config_dir(args)))


def _policy(args: argparse.Namespace) -> Policy:
    """Load ``--policies``, else the installed policies file, else no rules.

    Args:
        args: Parsed arguments with ``policies`` and ``config_dir``.

    Returns:
        Policy: The validated rules.

    Raises:
        PolicyError: If the policies file is invalid.
    """
    if args.policies is not None:
        return load_policy_file(args.policies)
    installed = _config_dir(args) / POLICIES_FILE
    return load_policy_file(installed) if installed.is_file() else Policy()


def _report(problems: Sequence[object]) -> int:
    """Print validation problems to standard error.

    Args:
        problems: Issues whose ``str`` is ``location: reason``.

    Returns:
        int: 1, the validation-error status.

    Raises:
        None
    """
    for problem in problems:
        print(f"vakr policy: {problem}", file=sys.stderr)
    print(f"vakr policy: {len(problems)} problem(s) found", file=sys.stderr)
    return 1


def run_explain(args: argparse.Namespace) -> int:
    """Explain the policy decision for every interface in the facts file.

    Args:
        args: ``facts``, ``policies``, ``config_dir``, and ``json``.

    Returns:
        int: 0 on success, 1 on validation errors.

    Raises:
        None
    """
    try:
        facts = load_facts_file(args.facts)
        policy = _policy(args)
        keywords = _keywords(args)
    except (PolicyError, ConfigError) as exc:
        return _report(exc.issues)
    explanations = [explain(item, policy, keywords) for item in facts]
    if args.json:
        print(json.dumps({"explanations": [to_dict(e) for e in explanations]}, indent=2))
    else:
        print("\n".join(render_text(e) for e in explanations), end="")
    return 0


def run_validate(args: argparse.Namespace) -> int:
    """Validate a policies file and the configured description keywords.

    Args:
        args: ``file`` and ``config_dir``.

    Returns:
        int: 0 when both are valid, 1 otherwise.

    Raises:
        None
    """
    problems: list[object] = []
    keywords, policy = 0, Policy()
    try:
        policy = load_policy_file(args.file)
    except PolicyError as exc:
        problems.extend(exc.issues)
    try:
        keywords = len(_keywords(args).keywords)
    except (PolicyError, ConfigError) as exc:
        problems.extend(exc.issues)
    if problems:
        return _report(problems)
    rules = len(policy.rules)
    print(f"policies valid: {rules} rule(s) in {args.file}; {keywords} description keyword(s)")
    for note in unapplied_settings(policy):  # Option A: the MVP polls whole devices
        print(f"warning: {note}")
    return 0
