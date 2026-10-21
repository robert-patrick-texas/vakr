"""Contract: session and kiosk-token repositories (SEC-010, AUTH-002, port extension E.12).

Subclass ``AuthSessionsContract`` in an adapter's test module; the
adapter's ``conftest.py`` supplies ``metadata_uow_factory``. Covers digest
lookup, uniqueness, the missing-user reference, per-user lists, touches,
deletes that a rollback undoes, and bounded expiry purges.

Dependencies:
    pytest; tests.contract.contract_auth_samples; vakr.ports.metadata.

Example:
    $ uv run pytest tests/contract -k AuthSessions
"""

from datetime import timedelta

import pytest

from tests.contract import contract_auth_samples as a
from tests.contract.contract_samples import T0
from vakr.domain.errors import DuplicateError, NotFoundError
from vakr.domain.ids import KioskTokenId, SessionId, UserId, new_id
from vakr.domain.users import User
from vakr.ports.metadata import MetadataUnitOfWorkFactory

IDLE = timedelta(minutes=30)


def _seed(factory: MetadataUnitOfWorkFactory) -> tuple[User, User]:
    """Commit two accounts.

    Args:
        factory: Opens units of work.

    Returns:
        tuple[User, User]: Alice and Bob.

    Raises:
        None
    """
    alice, bob = a.user(), a.user("bob")
    with factory() as uow:
        uow.users.add(alice)
        uow.users.add(bob)
        uow.commit()
    return alice, bob


class AuthSessionsContract:
    """Behaviour every metadata adapter shares for sessions and kiosk tokens."""

    def test_sec010_sessions_round_trip_by_digest(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """SEC-010: a session is found by ID and digest; IDs, digests, and users are checked.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        alice, _ = _seed(metadata_uow_factory)
        first = a.session(alice.id)
        with metadata_uow_factory() as uow:
            uow.sessions.add(first)
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.sessions.get(first.id) == first
            assert uow.sessions.get_by_token_hash(first.token_hash) == first
            assert uow.sessions.get_by_token_hash(a.digest()) is None
            assert uow.sessions.get(new_id(SessionId)) is None
            with pytest.raises(DuplicateError):
                uow.sessions.add(a.session(alice.id, id=first.id))
            with pytest.raises(DuplicateError):
                uow.sessions.add(a.session(alice.id, token_hash=first.token_hash))
            with pytest.raises(NotFoundError):
                uow.sessions.add(a.session(new_id(UserId)))

    def test_auth002_lists_touches_and_deletes(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """AUTH-002: per-user lists are oldest first and bounded; touch and delete work.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        alice, bob = _seed(metadata_uow_factory)
        mine = [a.session(alice.id, created_at=T0 + n * a.HOUR, last_seen_at=T0 + n * a.HOUR)
                for n in (2, 0, 1)]  # fmt: skip
        theirs = a.session(bob.id)
        with metadata_uow_factory() as uow:
            for s in (*mine, theirs):
                uow.sessions.add(s)
            uow.commit()
        with metadata_uow_factory() as uow:
            listed = uow.sessions.list_for_user(alice.id, limit=10)
            assert [s.created_at for s in listed] == [T0, T0 + a.HOUR, T0 + 2 * a.HOUR]
            assert len(uow.sessions.list_for_user(alice.id, limit=2)) == 2
            uow.sessions.touch(theirs.id, T0 + a.HOUR)
            uow.sessions.touch(new_id(SessionId), T0)  # a missing session is ignored
            assert uow.sessions.delete(listed[0].id)
            assert not uow.sessions.delete(listed[0].id)
            uow.commit()
        with metadata_uow_factory() as uow:
            touched = uow.sessions.get(theirs.id)
            assert touched is not None
            assert touched.last_seen_at == T0 + a.HOUR
            assert len(uow.sessions.list_for_user(alice.id, limit=10)) == 2
            assert uow.sessions.delete_for_user(alice.id) == 2
            uow.rollback()
        with metadata_uow_factory() as uow:
            assert len(uow.sessions.list_for_user(alice.id, limit=10)) == 2  # rolled back
            assert uow.sessions.delete_for_user(alice.id) == 2
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.sessions.list_for_user(alice.id, limit=10) == ()
            assert uow.sessions.get(theirs.id) is not None

    def test_auth002_purge_removes_expired_and_idle_in_batches(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """AUTH-002: purges take absolute-expired and idle sessions, at most ``limit`` a call.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        alice, _ = _seed(metadata_uow_factory)
        now = T0 + 12 * a.HOUR
        expired = a.session(alice.id, last_seen_at=now - timedelta(minutes=1))  # absolute
        idle = a.session(alice.id, last_seen_at=now - IDLE, expires_at=now + a.HOUR)
        live = a.session(alice.id, last_seen_at=now - IDLE + timedelta(seconds=1),
                         expires_at=now + a.HOUR)  # fmt: skip
        with metadata_uow_factory() as uow:
            for s in (expired, idle, live):
                uow.sessions.add(s)
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.sessions.purge_expired(now=now, idle_cutoff=now - IDLE, limit=1) == 1
            assert uow.sessions.purge_expired(now=now, idle_cutoff=now - IDLE, limit=10) == 1
            assert uow.sessions.purge_expired(now=now, idle_cutoff=now - IDLE, limit=10) == 0
            uow.commit()
        with metadata_uow_factory() as uow:
            assert [s.id for s in uow.sessions.list_for_user(alice.id, limit=10)] == [live.id]

    def test_e12_kiosk_tokens_round_trip_and_stay_bounded(
        self, metadata_uow_factory: MetadataUnitOfWorkFactory
    ) -> None:
        """E.12: kiosk tokens are unique by ID, digest, and label; listed by label; purged.

        Args:
            metadata_uow_factory: Opens units of work on one store.

        Returns:
            None

        Raises:
            None
        """
        wall = a.kiosk("noc-wall")
        lobby = a.kiosk("lobby", expires_at=T0 + a.HOUR)
        with metadata_uow_factory() as uow:
            uow.kiosk_tokens.add(wall)
            uow.kiosk_tokens.add(lobby)
            uow.commit()
        with metadata_uow_factory() as uow:
            tokens = uow.kiosk_tokens
            assert tokens.get(wall.id) == wall
            assert tokens.get_by_token_hash(lobby.token_hash) == lobby
            assert tokens.get_by_token_hash(a.digest()) is None
            assert [t.label for t in tokens.list_all(limit=10)] == ["lobby", "noc-wall"]
            assert len(tokens.list_all(limit=1)) == 1
            assert tokens.count() == 2
            for clash in (a.kiosk("x", id=wall.id), a.kiosk("y", token_hash=wall.token_hash),
                          a.kiosk("noc-wall")):  # fmt: skip
                with pytest.raises(DuplicateError):
                    tokens.add(clash)
            tokens.touch(wall.id, T0 + a.HOUR)
            tokens.touch(new_id(KioskTokenId), T0)
            assert tokens.purge_expired(now=T0 + a.HOUR, limit=10) == 1
            uow.commit()
        with metadata_uow_factory() as uow:
            stored = uow.kiosk_tokens.get(wall.id)
            assert stored is not None
            assert stored.last_used_at == T0 + a.HOUR
            assert uow.kiosk_tokens.get(lobby.id) is None
            assert uow.kiosk_tokens.delete(wall.id)
            assert not uow.kiosk_tokens.delete(wall.id)
            uow.commit()
        with metadata_uow_factory() as uow:
            assert uow.kiosk_tokens.count() == 0
