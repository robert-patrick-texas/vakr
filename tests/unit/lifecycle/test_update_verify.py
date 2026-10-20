"""UPD-T02 (tampered or unsigned releases refused) and UPD-T04 (offline ``--file`` install).

Dependencies:
    tests.unit.lifecycle.helpers: Harness, signer, and release builder.

Example:
    $ uv run pytest tests/unit/lifecycle/test_update_verify.py
"""

import hashlib
from pathlib import Path

import pytest

from tests.unit.lifecycle.helpers import (
    FakeHttp,
    Harness,
    Signer,
    build_release,
    make_harness,
    new_signer,
)
from vakr.lifecycle.update.policy import Request
from vakr.lifecycle.update.process import run_command
from vakr.lifecycle.update.releases import ReleaseError
from vakr.lifecycle.update.state import load_state
from vakr.lifecycle.update.verify import (
    Artifacts,
    ReleaseTrust,
    VerificationError,
    expected_digest,
    verify_release,
)
from vakr.lifecycle.update.versions import Version

V = Version.parse
DEB = "vakr_0.3.0_amd64.deb"


def yes(question: str) -> bool:
    """Confirm every question.

    Args:
        question: Ignored prompt.

    Returns:
        bool: Always ``True``.

    Raises:
        None
    """
    del question
    return True


def _assert_unchanged(h: Harness) -> None:
    """Check that a refused update changed nothing on the host.

    Args:
        h: Harness.

    Returns:
        None

    Raises:
        None
    """
    assert h.installer.installed == []
    assert h.runner.calls == []
    assert not h.kit.layout.backups_dir.exists()
    assert load_state(h.kit.layout.state_file).result == "refused"


def test_upd_t02_tampered_package_is_refused(harness: Harness) -> None:
    """UPD-T02: a package whose bytes differ from SHA256SUMS is never installed.

    Args:
        harness: Updater with fakes.

    Returns:
        None

    Raises:
        None
    """
    harness.publish("0.3.0")
    harness.http.files[harness.asset_url("0.3.0", DEB)] = b"evil payload"
    outcome = harness.updater.update(V("0.2.0"), Request(), yes)
    assert (outcome.status, outcome.exit_code) == ("refused", 1)
    assert "checksum mismatch" in outcome.message
    _assert_unchanged(harness)
    assert "update.verify.failed" in harness.ids()


def test_upd_t02_tampered_sums_fail_signature(harness: Harness) -> None:
    """UPD-T02: rewriting SHA256SUMS to match a tampered package breaks the signature.

    Args:
        harness: Updater with fakes.

    Returns:
        None

    Raises:
        None
    """
    harness.publish("0.3.0")
    evil = b"evil payload"
    harness.http.files[harness.asset_url("0.3.0", DEB)] = evil
    sums = f"{hashlib.sha256(evil).hexdigest()}  {DEB}\n".encode()
    harness.http.files[harness.asset_url("0.3.0", "SHA256SUMS")] = sums
    outcome = harness.updater.update(V("0.2.0"), Request(), yes)
    assert outcome.status == "refused"
    assert "signature verification failed" in outcome.message
    _assert_unchanged(harness)


def test_upd_t02_signature_from_another_key_is_refused(harness: Harness, tmp_path: Path) -> None:
    """UPD-T02: a valid signature by a key not in allowed_signers is refused.

    Args:
        harness: Updater with fakes.
        tmp_path: Temporary directory.

    Returns:
        None

    Raises:
        None
    """
    harness.publish("0.3.0")
    intruder = new_signer(tmp_path / "intruder")
    sums = tmp_path / "SHA256SUMS"
    sums.write_bytes(harness.http.files[harness.asset_url("0.3.0", "SHA256SUMS")])
    forged = intruder.sign(sums).read_bytes()
    harness.http.files[harness.asset_url("0.3.0", "SHA256SUMS.sig")] = forged
    assert harness.updater.update(V("0.2.0"), Request(), yes).status == "refused"
    _assert_unchanged(harness)


def test_upd_t02_missing_signature_asset_stops_before_change(harness: Harness) -> None:
    """UPD-T02: a release without SHA256SUMS.sig is rejected before any change.

    Args:
        harness: Updater with fakes.

    Returns:
        None

    Raises:
        None
    """
    harness.publish("0.3.0")
    harness.releases[0]["assets"] = [
        a for a in harness.releases[0]["assets"] if a["name"] != "SHA256SUMS.sig"
    ]
    harness.http.publish("https://api.example", "o/r", harness.releases)
    with pytest.raises(ReleaseError, match=r"no asset SHA256SUMS\.sig"):
        harness.updater.update(V("0.2.0"), Request(), yes)
    assert harness.installer.installed == []
    assert not harness.kit.layout.backups_dir.exists()


def _offline_release(tmp_path: Path, signer: Signer, semver: str = "0.3.0") -> Artifacts:
    """Build hand-transferred release files in a download directory.

    Args:
        tmp_path: Temporary directory.
        signer: Release signer.
        semver: Version to build.

    Returns:
        Artifacts: Package, sums, and signature paths.

    Raises:
        None
    """
    directory = tmp_path / "usb" / semver
    package = build_release(directory, signer, V(semver).debian(), b"offline package")
    return Artifacts(package, directory / "SHA256SUMS", directory / "SHA256SUMS.sig")


def test_upd_t04_offline_file_install(tmp_path: Path, signer: Signer) -> None:
    """UPD-T04: ``--file`` with verified artifacts succeeds with no network at all.

    Args:
        tmp_path: Temporary directory.
        signer: Release signer.

    Returns:
        None

    Raises:
        None
    """
    h = make_harness(tmp_path / "host", signer, None)
    files = _offline_release(tmp_path, signer)
    installed_from: list[Path] = []
    h.installer.on_install = installed_from.append
    outcome = h.updater.install_files(V("0.2.0"), Request(), files, yes)
    assert outcome.status == "updated"
    assert h.installer.installed == [DEB]
    # The verified copy inside the root-owned cache is installed, never the original.
    assert installed_from == [h.kit.layout.cache_root / "0.3.0" / DEB]
    assert (h.kit.layout.cache_root / "0.3.0" / DEB).read_bytes() == b"offline package"
    assert "update.rollback.unavailable" in h.ids()
    assert h.http.log == []
    again = h.updater.install_files(V("0.3.0"), Request(if_newer=True), files, yes)
    assert again.status == "up-to-date"
    with pytest.raises(ReleaseError, match="use --file"):
        h.updater.update(V("0.3.0"), Request(), yes)


def test_offline_file_refusals(tmp_path: Path, signer: Signer) -> None:
    """``--file`` refuses misnamed or tampered packages and missing signatures.

    Args:
        tmp_path: Temporary directory.
        signer: Release signer.

    Returns:
        None

    Raises:
        None
    """
    h = make_harness(tmp_path / "host", signer, FakeHttp())
    files = _offline_release(tmp_path, signer)
    renamed = files.package.with_name("vakr-latest.deb")
    files.package.rename(renamed)
    misnamed = h.updater.install_files(
        V("0.2.0"), Request(), Artifacts(renamed, files.sums, files.signature), yes
    )
    assert (misnamed.status, misnamed.exit_code) == ("refused", 1)
    renamed.rename(files.package)
    files.package.write_bytes(b"tampered")
    assert h.updater.install_files(V("0.2.0"), Request(), files, yes).status == "refused"
    files.signature.unlink()
    unsigned = h.updater.install_files(V("0.2.0"), Request(), files, yes)
    assert unsigned.status == "refused"
    assert "missing file(s)" in unsigned.message
    assert "SHA256SUMS.sig" in unsigned.message
    bad_version = Artifacts(
        files.package.with_name("vakr_x.y_amd64.deb"), files.sums, files.signature
    )
    assert h.updater.install_files(V("0.2.0"), Request(), bad_version, yes).status == "refused"
    assert h.installer.installed == []


def test_verification_details(tmp_path: Path, signer: Signer) -> None:
    """Checksum parsing is strict, and the signers file and namespace are enforced.

    Args:
        tmp_path: Temporary directory.
        signer: Release signer.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(VerificationError, match="line 1 is malformed"):
        expected_digest(b"nonsense\n", DEB)
    digest = "a" * 64
    twice = f"{digest}  {DEB}\n{digest} *{DEB}\n".encode()
    with pytest.raises(VerificationError, match="more than once"):
        expected_digest(twice, DEB)
    with pytest.raises(VerificationError, match="not listed"):
        expected_digest(f"{digest}  other.deb\n\n".encode(), DEB)
    files = _offline_release(tmp_path, signer)
    good = ReleaseTrust(signer.allowed, "vakr-release", "vakr-release", signer.ssh_keygen)
    verify_release(files, good, run_command)
    wrong_ns = ReleaseTrust(signer.allowed, "vakr-release", "other-namespace", signer.ssh_keygen)
    with pytest.raises(VerificationError, match="signature verification failed"):
        verify_release(files, wrong_ns, run_command)
    missing = ReleaseTrust(tmp_path / "absent", "vakr-release", "vakr-release", signer.ssh_keygen)
    with pytest.raises(VerificationError, match="signers file missing"):
        verify_release(files, missing, run_command)
    files.sums.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(VerificationError, match="implausibly large"):
        verify_release(files, good, run_command)
    files.sums.unlink()
    with pytest.raises(VerificationError, match="cannot read SHA256SUMS"):
        verify_release(files, good, run_command)


def test_offline_rc_package_with_github_spelling(tmp_path: Path, signer: Signer) -> None:
    """A release candidate downloaded as ``0.4.0.rc.1`` installs under its canonical name.

    Args:
        tmp_path: Temporary directory.
        signer: Release signer.

    Returns:
        None

    Raises:
        None
    """
    h = make_harness(tmp_path / "host", signer, None)
    files = _offline_release(tmp_path, signer, "0.4.0-rc.1")
    github_name = files.package.with_name("vakr_0.4.0.rc.1_amd64.deb")
    files.package.rename(github_name)
    renamed = Artifacts(github_name, files.sums, files.signature)
    assert h.updater.install_files(V("0.3.0"), Request(), renamed, yes).status == "updated"
    assert h.installer.installed == ["vakr_0.4.0~rc.1_amd64.deb"]
