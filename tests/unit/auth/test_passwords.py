"""Argon2id hashing, rehash detection, the password policy, and the hashing bound (SEC-009, E.12).

Dependencies:
    pytest, argon2-cffi; vakr.auth.passwords; tests.unit.auth.auth_testkit.

Example:
    $ uv run pytest tests/unit/auth/test_passwords.py
"""

import threading
from typing import Literal

import argon2
import pytest

from tests.unit.auth.auth_testkit import cheap_hasher, new_password
from vakr.auth.passwords import AuthBusyError, PasswordPolicy, Passwords
from vakr.credentials.secret import Secret


def test_sec009_hash_is_argon2id_with_parameters_and_verifies() -> None:
    """SEC-009: hashes are Argon2id PHC strings carrying their parameters.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    passwords, pw = Passwords(cheap_hasher()), new_password()
    stored = passwords.hash(Secret(pw))
    assert stored.startswith("$argon2id$v=19$m=8,t=1,p=1$")
    assert pw not in stored
    assert passwords.verify(stored, Secret(pw))
    assert not passwords.verify(stored, Secret(pw + "x"))
    assert stored != passwords.hash(Secret(pw))  # a fresh salt every time


def test_sec009_needs_rehash_when_parameters_change() -> None:
    """SEC-009: a hash made with other parameters is flagged for rehash; current ones are not.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    old, new = Passwords(cheap_hasher(1)), Passwords(cheap_hasher(2))
    pw = Secret(new_password())
    legacy = old.hash(pw)
    assert not old.needs_rehash(legacy)
    assert new.needs_rehash(legacy)
    assert new.verify(legacy, pw)  # old hashes keep working until they are replaced
    assert not new.needs_rehash(new.hash(pw))


def test_production_defaults_are_the_rfc9106_low_memory_profile() -> None:
    """E.12: without injection the hasher uses argon2-cffi's defaults (Argon2id, 64 MiB, t=3).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    default = argon2.PasswordHasher()
    assert (default.type, default.time_cost, default.memory_cost, default.parallelism) == (
        argon2.Type.ID, 3, 65536, 4
    )  # fmt: skip
    legacy = Passwords(cheap_hasher()).hash(Secret(new_password()))
    assert Passwords().needs_rehash(legacy)  # the injected cheap profile is not the default


def test_damaged_hash_never_verifies_and_asks_for_rehash(caplog: pytest.LogCaptureFixture) -> None:
    """SEC-009: a corrupt stored hash fails closed, is logged without its value, and rehashes.

    Args:
        caplog: Captures the warning.

    Returns:
        None

    Raises:
        None
    """
    passwords, pw = Passwords(cheap_hasher()), new_password()
    damaged = "$argon2id$garbage-" + pw[-6:]
    assert not passwords.verify(damaged, Secret(pw))
    assert passwords.needs_rehash(damaged)
    assert "not a valid Argon2 hash" in caplog.text
    assert damaged not in caplog.text


def test_nfkc_normalisation_makes_composed_and_decomposed_forms_equal() -> None:
    """E.12: the same password typed in two Unicode forms verifies (NFKC before hashing).

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    passwords = Passwords(cheap_hasher())
    composed, decomposed = "café-" + new_password(), "café-"
    stored = passwords.hash(Secret(composed))
    assert passwords.verify(stored, Secret(decomposed + composed[5:]))


@pytest.mark.parametrize(
    ("candidate", "rule"),
    [
        ("short-but-ok", "at least 15"),
        ("x" * 257, "at most 256"),
        ("aaaaaaaaaaaaaaaaaaaa", "single repeated character"),
        ("line\nbreak-password-1234", "control characters"),
        ("Alice.Example.Com", "must not be the username"),
    ],
)
def test_policy_rejects_and_never_quotes_the_password(candidate: str, rule: str) -> None:
    """E.12: each broken rule is named, and no message contains the candidate.

    Args:
        candidate: Unacceptable password.
        rule: Words the message must contain.

    Returns:
        None

    Raises:
        None
    """
    problems = PasswordPolicy().problems(Secret(candidate), "alice.example.com")
    assert any(rule in p for p in problems)
    assert all(candidate not in p for p in problems)


def test_policy_accepts_a_long_passphrase_without_composition_rules() -> None:
    """E.12 (NIST SP 800-63B): length is required; character classes are not.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    assert PasswordPolicy().problems(Secret("correct horse battery staple"), "alice") == []
    assert PasswordPolicy(min_length=8).problems(Secret("lowercase"), "alice") == []


def test_hashing_concurrency_is_bounded() -> None:
    """Bounded concurrency: with every slot taken, a hash fails as busy instead of queueing.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    passwords = Passwords(cheap_hasher(), max_concurrent=1, wait_s=0.01)
    stored = passwords.hash(Secret(new_password()))
    taken = threading.Event()
    release = threading.Event()

    def hold() -> None:
        """Occupy the only slot until released.

        Args:
            None

        Returns:
            None

        Raises:
            None
        """
        passwords._slots.acquire()  # simulate a long-running hash
        taken.set()
        release.wait(5)
        passwords._slots.release()

    worker = threading.Thread(target=hold)
    worker.start()
    taken.wait(5)
    with pytest.raises(AuthBusyError):
        passwords.verify(stored, Secret("anything"))
    release.set()
    worker.join()
    assert not passwords.verify(stored, Secret("anything"))  # the slot is free again


def test_verify_dummy_costs_a_verification() -> None:
    """AUTH-004: the unknown-account path runs one real Argon2 verification.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    calls: list[str] = []

    class Counting(argon2.PasswordHasher):
        """A hasher that counts verifications."""

        def verify(self, hash: str | bytes, password: str | bytes) -> Literal[True]:  # noqa: A002
            """Count, then verify.

            Args:
                hash: Stored hash.
                password: Candidate.

            Returns:
                Literal[True]: The library's answer on a match.

            Raises:
                VerificationError: On mismatch, from the library.
            """
            calls.append("verify")
            return super().verify(hash, password)

    passwords = Passwords(Counting(time_cost=1, memory_cost=8, parallelism=1))
    passwords.warm_up()
    assert calls == []  # warming hashes, it does not verify
    passwords.verify_dummy(Secret(new_password()))
    passwords.verify_dummy(Secret(new_password()))
    assert calls == ["verify", "verify"]
