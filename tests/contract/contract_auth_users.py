"""Contract: user and login-failure repositories (SEC-005, SEC-009, SEC-012, port extension E.12).

Subclass ``AuthUsersContract`` in an adapter's test module; the adapter's
``conftest.py`` supplies ``metadata_uow_factory``. Covers canonical,
case-insensitive usernames, the update rules, the rehash compare-and-set
of ``record_login`` (SEC-009), failure-run counting and reset (AUTH-004),
bounded purges, and that none of it moves the change counter.

Dependencies:
    pytest; tests.contract.contract_auth_samples; vakr.ports.metadata.

Example:
    $ uv run pytest tests/contract -k AuthUsers
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from tests.contract import contract_auth_samples as a
from tests.contract.contract_samples import T0
from vakr.domain.errors import ConflictError, DuplicateError, NotFoundError, ValidationError
from vakr.domain.ids import UserId, new_id
from vakr.domain.paging import PageRequest
from vakr.domain.users import Role
from vakr.ports.metadata import MetadataUnitOfWorkFactory, UserFilter

RESET = timedelta(minutes=15)


class AuthUsersContract:
    """Behaviour every metadata adapter shares for users and login failures."""

    def test_sec005_users_round_trip_with_canonical_usernames(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """SEC-005: accounts round-trip; usernames match after trimming and lower-casing.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        alice = a.user()
        with metadata_uow_factory() as uow:
            uow.users.add(alice)
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.users.get(alice.id) == alice
            assert uow.users.get_by_username("  ALICE ") == alice
            assert uow.users.get_by_username("not a username!") is None
            assert uow.users.get_by_username("x" * 5000) is None
            assert uow.users.get(new_id(UserId)) is None
            with pytest.raises(DuplicateError):
                uow.users.add(a.user())  # same canonical username
            with pytest.raises(DuplicateError):
                uow.users.add(replace(a.user("bob"), id=alice.id))
            with pytest.raises(ValidationError):
                uow.users.add(a.user("carol", version=2))

    def test_sec005_users_list_in_order_with_filters(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """SEC-005: lists are ``(username, id)`` pages with totals and role/state filters.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        carol = a.user("carol", role=Role.OPERATOR, enabled=False)
        bob = a.user("bob", role=Role.OPERATOR)
        alice = a.user("alice")
        with metadata_uow_factory() as uow:
            for u in (carol, bob, alice):
                uow.users.add(u)
            uow.commit()
        with metadata_uow_factory() as uow:
            first = uow.users.list_page(PageRequest(limit=2))
            rest = uow.users.list_page(PageRequest(limit=2, cursor=first.next_cursor))
            assert [u.username for u in (*first.items, *rest.items)] == ["alice", "bob", "carol"]
            assert first.total == 3
            assert rest.next_cursor is None
            ops = uow.users.list_page(PageRequest(), UserFilter(role=Role.OPERATOR))
            assert [u.username for u in ops.items] == ["bob", "carol"]
            on = uow.users.list_page(PageRequest(), UserFilter(role=Role.OPERATOR, enabled=True))
            assert [u.username for u in on.items] == ["bob"]
            off = uow.users.list_page(PageRequest(), UserFilter(enabled=False))
            assert off.total == 1

    def test_db003_user_update_rules(self, metadata_uow_factory: MetadataUnitOfWorkFactory) -> None:
        """DB-003: update checks existence, version, and fixed fields; keeps ``last_login_at``.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        alice = a.user()
        with metadata_uow_factory() as uow:
            uow.users.add(alice)
            uow.users.record_login(alice.id, at=T0 + a.HOUR, verified_hash=alice.password_hash)
            uow.commit()
        with metadata_uow_factory() as uow:
            stored = uow.users.update(replace(alice, role=Role.OPERATOR, updated_at=T0 + a.HOUR))
            assert (stored.version, stored.role) == (2, Role.OPERATOR)
            assert stored.last_login_at == T0 + a.HOUR  # not the caller's stale None
            with pytest.raises(ConflictError):
                uow.users.update(replace(alice, display_name="Old copy"))
            with pytest.raises(NotFoundError):
                uow.users.update(a.user("ghost"))
            for fixed in ({"username": "alice2"}, {"created_at": T0 + a.HOUR}):
                with pytest.raises(ValidationError):
                    uow.users.update(replace(stored, **fixed))
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.users.get(alice.id) == stored

    def test_sec009_record_login_rehash_is_compare_and_set(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """SEC-009: a rehash replaces only the hash that was verified; ``version`` never moves.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        alice = a.user()
        upgraded, changed_meanwhile = a.fake_hash(), a.fake_hash()
        with metadata_uow_factory() as uow:
            uow.users.add(alice)
            assert uow.users.record_login(alice.id, at=T0, verified_hash=alice.password_hash,
                                          new_hash=upgraded)  # fmt: skip
            uow.commit()
        with metadata_uow_factory() as uow:
            stored = uow.users.get(alice.id)
            assert stored is not None
            assert (stored.password_hash, stored.version, stored.last_login_at) == (
                upgraded, 1, T0
            )  # fmt: skip
            uow.users.update(replace(stored, password_hash=changed_meanwhile))
            stale = uow.users.record_login(alice.id, at=T0 + a.HOUR, verified_hash=upgraded,
                                           new_hash=a.fake_hash())  # fmt: skip
            assert stale is False
            assert not uow.users.record_login(alice.id, at=T0, verified_hash=changed_meanwhile)
            uow.commit()
        with metadata_uow_factory() as uow:
            final = uow.users.get(alice.id)
            assert final is not None
            assert (final.password_hash, final.version) == (changed_meanwhile, 2)
            with pytest.raises(NotFoundError):
                uow.users.record_login(new_id(UserId), at=T0, verified_hash=a.fake_hash())

    def test_auth004_failure_runs_count_reset_and_clear(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """AUTH-004: failures count per (account, source), restart when quiet, clear on success.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        key, other = a.account_key("alice"), a.account_key("bob")
        with metadata_uow_factory() as uow:
            failures = uow.login_failures
            for n in range(1, 4):
                at = T0 + n * timedelta(seconds=10)
                assert (
                    failures.record_failure(key, a.SOURCE, at=at, reset_before=at - RESET).failures
                    == n
                )
            assert (
                failures.record_failure(
                    key, "198.51.100.7", at=T0, reset_before=T0 - RESET
                ).failures
                == 1
            )
            assert (
                failures.record_failure(other, a.SOURCE, at=T0, reset_before=T0 - RESET).failures
                == 1
            )
            uow.commit()
        with metadata_uow_factory() as uow:
            run = uow.login_failures.get(key, a.SOURCE)
            assert run is not None
            assert (run.failures, run.first_failed_at, run.last_failed_at) == (
                3, T0 + timedelta(seconds=10), T0 + timedelta(seconds=30)
            )  # fmt: skip
            later = run.last_failed_at + RESET + timedelta(seconds=1)
            restarted = uow.login_failures.record_failure(key, a.SOURCE, at=later,
                                                          reset_before=later - RESET)  # fmt: skip
            assert (restarted.failures, restarted.first_failed_at) == (1, later)
            uow.login_failures.clear(key, a.SOURCE)
            assert uow.login_failures.get(key, a.SOURCE) is None
            assert uow.login_failures.count() == 2
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.login_failures.get(key, a.SOURCE) is None
            assert uow.login_failures.get(other, a.SOURCE) is not None

    def test_auth004_failure_purge_is_bounded_and_oldest_first(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """AUTH-004, E.12: purges delete at most ``limit`` runs, the quietest first.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        keys = [a.account_key(f"user{n}") for n in range(5)]
        with metadata_uow_factory() as uow:
            for n, key in enumerate(keys):
                at = T0 + n * a.HOUR
                uow.login_failures.record_failure(key, a.SOURCE, at=at, reset_before=at - RESET)
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.login_failures.purge(T0 + 3 * a.HOUR, limit=2) == 2
            assert uow.login_failures.get(keys[0], a.SOURCE) is None
            assert uow.login_failures.get(keys[1], a.SOURCE) is None
            assert uow.login_failures.get(keys[2], a.SOURCE) is not None
            assert uow.login_failures.purge(T0 + 3 * a.HOUR, limit=10) == 1
            assert uow.login_failures.purge(T0, limit=10) == 0
            assert uow.login_failures.count() == 2
            uow.rollback()
        with metadata_uow_factory() as uow:
            assert uow.login_failures.count() == 5  # rolled back: nothing purged

    def test_auth_records_leave_the_change_counter_alone(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """6.2: logins and failures are not collection metadata; the counter does not move.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        with metadata_uow_factory() as uow:
            before = uow.change_counter()
            alice = a.user()
            uow.users.add(alice)
            uow.sessions.add(a.session(alice.id))
            uow.kiosk_tokens.add(a.kiosk())
            uow.login_failures.record_failure(a.account_key(), a.SOURCE, at=T0,
                                              reset_before=T0 - RESET)  # fmt: skip
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.change_counter() == before
