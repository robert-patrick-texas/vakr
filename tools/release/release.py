#!/usr/bin/env python3
# /// script
# requires-python = ">=3.15"
# dependencies = []
#
# # ty checks a PEP 723 script in its own environment; this lets it resolve
# # the sibling release_* modules, as Python does when running the script.
# [tool.ty.environment]
# extra-paths = ["."]
# ///
# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0
"""Build, checksum, sign, verify, tag, and publish a release (blueprint 28.7, ADR-026).

Releases travel from the development host to production sites that trust
nothing but the signed ``SHA256SUMS`` (UPD-001). This tool is the only path
to a release: it checks that the tree is clean, on the release branch, not
yet tagged, and green in CI; runs the configured clean-container build;
checksums and SSH-signs the artifacts; verifies that signature against the
signers file the package ships; and only then tags, pushes, and creates the
GitHub release with notes from the changelog. Everything project-specific
comes from ``release.toml`` (section 29).

Dependencies:
    release_config, release_checks, release_artifacts, release_publish,
    release_version, release_proc: Sibling modules; standard library only.
    External: git, gh (authenticated), ssh-keygen, and the build command.

Safety:
    ``--dry-run`` runs every check and step except tag, push, and release,
    and signs with a throwaway key when the real one is absent. The private
    key is used by path and never read. CI cannot sign: the key lives only
    on the development host.

Example:
    $ uv run tools/release/release.py --dry-run
    $ uv run tools/release/release.py --print-version deb
    7.2.0~rc.1
"""

import argparse
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import release_artifacts as artifacts
import release_checks as checks
from release_config import ConfigError, ReleaseConfig, load_config
from release_proc import CommandError, Runner
from release_publish import changelog_notes, publish, publish_commands
from release_version import VERSION_FORMS, ReleaseVersion, VersionError, read_version_file

DEFAULT_CONFIG = Path(__file__).with_name("release.toml")


def preflight(
    cfg: ReleaseConfig, runner: Runner, version: ReleaseVersion, environ: Mapping[str, str]
) -> list[checks.Outcome]:
    """Run every check that must pass before a real release starts.

    Args:
        cfg: Release configuration.
        runner: Command runner in the repository.
        version: Version being released.
        environ: Process environment.

    Returns:
        list[checks.Outcome]: One outcome per check, in a stable order.

    Raises:
        None
    """
    try:
        sha = checks.head_commit(runner)
        ci = checks.ci_green(runner, cfg.repo, sha)
    except CommandError as exc:
        ci = checks.Outcome("CI green for HEAD", False, str(exc))
    notes = changelog_notes(_read(cfg.changelog), version.semver)
    entries = artifacts.signer_entries(cfg.allowed_signers)
    return [
        checks.no_version_override(environ),
        checks.clean_tree(runner),
        checks.on_branch(runner, cfg.branch),
        checks.tag_absent(runner, cfg.remote, version.tag),
        ci,
        checks.Outcome(
            f"{cfg.changelog.name} has a {version.semver} section",
            notes is not None,
            "found" if notes else f"add a '## {version.semver}' section with release notes",
        ),
        checks.Outcome(
            f"{cfg.allowed_signers.name} lists a signer",
            entries > 0,
            f"{entries} signer(s)" if entries else "placeholder only; add the release public key",
        ),
    ]


def build_sign_verify(
    cfg: ReleaseConfig, runner: Runner, version: ReleaseVersion, *, dry_run: bool, workdir: Path
) -> list[Path]:
    """Build, stage, checksum, sign, and verify; return the files to publish.

    Args:
        cfg: Release configuration.
        runner: Command runner in the repository.
        version: Version being released.
        dry_run: Allow a throwaway key when the real key is absent.
        workdir: Private temporary directory for a throwaway key.

    Returns:
        list[Path]: Staged artifacts, then ``SHA256SUMS`` and its signature.

    Raises:
        CommandError: If the build, signing, or a tool fails.
        artifacts.ArtifactError: If artifacts are missing, the real key is
            absent outside a dry run, or verification fails.
        ConfigError: If an artifact pattern is malformed.
    """
    env = {k: v for k, v in runner.env.items() if k != "VERSION_OVERRIDE"}
    print(f"build: {' '.join(cfg.build_command)}", flush=True)
    runner.run(cfg.build_command, capture=False, env=env)
    out_dir = cfg.root / cfg.expand(cfg.output_dir, version)
    staged = artifacts.stage(artifacts.collect(cfg, version), out_dir)
    sums = artifacts.write_sha256sums(staged, out_dir)
    print(f"checksums: {sums} ({len(staged)} artifact(s))")
    key, signers = cfg.signing_key, cfg.allowed_signers
    if not key.is_file():
        if not dry_run:
            raise artifacts.ArtifactError(f"signing key not found: {key}")
        key, signers = artifacts.throwaway_signer(runner, workdir, cfg.identity, cfg.namespace)
        print(f"sign: {cfg.signing_key} absent; dry run uses a throwaway key")
    signature = artifacts.sign(runner, key, cfg.namespace, sums)
    ok, detail = artifacts.verify(runner, signers, cfg.identity, cfg.namespace, sums)
    print(f"verify against {signers}: {detail}")
    if not ok:
        raise artifacts.ArtifactError("signature does not verify against the trusted signers")
    return [*staged, sums, signature]


def release(
    cfg: ReleaseConfig, runner: Runner, *, dry_run: bool, environ: Mapping[str, str]
) -> int:
    """Run a complete release, or a dry run of one.

    Args:
        cfg: Release configuration.
        runner: Command runner in the repository.
        dry_run: Do everything except tag, push, and release.
        environ: Process environment, checked for ``VERSION_OVERRIDE``.

    Returns:
        int: 0 when the release (or a dry run that a real release would
        pass) succeeded, 1 otherwise.

    Raises:
        None
    """
    version = read_version_file(cfg.version_file)
    mode = "dry run" if dry_run else "release"
    print(f"{mode}: {cfg.package} {version.semver} ({version.tag}) to {cfg.repo}")
    outcomes = preflight(cfg, runner, version, environ)
    for outcome in outcomes:
        print(f"[{'ok' if outcome.ok else 'FAIL':>4}] {outcome.name}: {outcome.detail}")
    blocked = [o for o in outcomes if not o.ok]
    if blocked and not dry_run:
        print(f"release: stopped by {len(blocked)} failed check(s); nothing was built")
        return 1
    try:
        with tempfile.TemporaryDirectory(prefix="release-") as tmp:
            files = build_sign_verify(cfg, runner, version, dry_run=dry_run, workdir=Path(tmp))
        notes = files[-1].with_name("release-notes.txt")
        notes.write_text(changelog_notes(_read(cfg.changelog), version.semver) or "", "utf-8")
        publish(runner, publish_commands(cfg, version, notes, files), dry_run=dry_run)
    except (CommandError, artifacts.ArtifactError, ConfigError, VersionError, OSError) as exc:
        print(f"{mode}: FAILED: {exc}")
        return 1
    if blocked:
        print(f"dry run: complete; a real release would stop at {len(blocked)} failed check(s)")
        return 1
    print(f"{mode}: complete")
    return 0


def _read(path: Path) -> str:
    """Return a text file's content, or an empty string when it is missing.

    Args:
        path: File to read.

    Returns:
        str: The content, or ``""``.

    Raises:
        None
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run a release, a dry run, or a version query.

    Args:
        argv: Arguments without the program name; ``None`` means ``sys.argv``.

    Returns:
        int: 0 on success, 1 when a release step fails, 2 on usage or
        configuration errors.

    Raises:
        SystemExit: Raised by argparse for ``--help`` and usage errors.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument("--dry-run", action="store_true", help="no tag, push, or release")
    parser.add_argument("--print-version", choices=VERSION_FORMS, metavar="FORM")
    parser.add_argument("--version", help="version to print instead of reading VERSION")
    args = parser.parse_args(argv)
    try:
        if args.print_version:
            source = args.version or (args.root / "VERSION").read_text(encoding="utf-8")
            print(ReleaseVersion.parse(source).render(args.print_version))
            return 0
        cfg = load_config(args.config, args.root.resolve())
        return release(cfg, Runner(cfg.root), dry_run=args.dry_run, environ=os.environ)
    except (ConfigError, VersionError, OSError) as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
