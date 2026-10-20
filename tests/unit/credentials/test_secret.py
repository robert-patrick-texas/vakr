"""Unit tests for ``Secret``: redacted renderings, refusal paths, equality, hashing.

Covers the value-object half of SEC-003 and blueprint 15.2 ("credential value
objects shall implement redacted representations").

Dependencies:
    pytest: Assertions and parametrisation.

Example:
    $ uv run pytest tests/unit/credentials/test_secret.py
"""

import copy
import json
import pickle

import pytest

from tests.unit.credentials.credentials_testkit import FakeSecrets
from vakr.credentials.secret import REDACTED, Secret


def test_every_rendering_is_redacted(fake: FakeSecrets) -> None:
    """``repr``, ``str``, ``format``, ``%`` and f-strings all render ``Secret('***')``.

    Args:
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    s = Secret(fake.community)
    percent = "%s %r" % (s, s)  # noqa: UP031 - the old formatting path is under test
    rendered = (repr(s), str(s), format(s), format(s, ">80"), f"{s!r}", percent)
    assert all(REDACTED in r for r in rendered)
    assert fake.community not in "".join(rendered)
    assert f"{s:>80}" == REDACTED  # padding would reveal nothing, and is ignored


def test_reveal_is_the_only_accessor(fake: FakeSecrets) -> None:
    """The value comes back only through ``reveal()``; no ``__dict__`` exposes it.

    Args:
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    s = Secret(fake.auth)
    assert s.reveal() == fake.auth
    assert not isinstance(s, str)
    with pytest.raises(TypeError):
        vars(s)


def test_secret_refuses_non_strings() -> None:
    """Only ``str`` can be wrapped; the error names the type only.

    Args:
        None

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(TypeError, match="not bytes"):
        Secret(b"raw")  # ty: ignore[invalid-argument-type]


def test_secret_is_immutable(fake: FakeSecrets) -> None:
    """Attributes cannot be set or deleted.

    Args:
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    s = Secret(fake.community)
    with pytest.raises(AttributeError):
        s._value = "other"  # ty: ignore[invalid-assignment]
    with pytest.raises(AttributeError):
        del s._value  # ty: ignore[invalid-assignment]
    assert s.reveal() == fake.community


def test_equality_between_secrets(fake: FakeSecrets) -> None:
    """Equal values compare equal, distinct values do not, and other types are unequal.

    Args:
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    assert Secret(fake.community) == Secret(fake.community)
    assert Secret(fake.community) != Secret(fake.auth)
    assert Secret("é-non-ascii") == Secret("é-non-ascii")
    assert Secret(fake.community) != 42
    assert Secret(fake.community) is not None


@pytest.mark.parametrize("other", ["plain", b"plain"])
def test_comparison_with_plain_strings_is_refused(other: str | bytes) -> None:
    """Comparing with ``str`` or ``bytes`` raises instead of silently returning False.

    Args:
        other: A plain string or bytes value.

    Returns:
        None

    Raises:
        None
    """
    with pytest.raises(TypeError, match="only with another Secret") as info:
        _ = Secret("plain") == other
    assert "plain" not in str(info.value).replace("Secret", "")


def test_hash_is_consistent_and_hides_the_value(fake: FakeSecrets) -> None:
    """Equal secrets hash equally; the hash is not the value's own hash.

    Args:
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    assert hash(Secret(fake.community)) == hash(Secret(fake.community))
    assert hash(Secret(fake.community)) != hash(fake.community)
    assert len({Secret(fake.community), Secret(fake.community), Secret(fake.auth)}) == 2


def test_pickle_and_json_are_refused(fake: FakeSecrets) -> None:
    """Serialisers cannot write the value; their errors do not contain it.

    Args:
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    s = Secret(fake.community)
    for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
        with pytest.raises(TypeError, match="cannot be pickled") as info:
            pickle.dumps(s, protocol=protocol)
        assert fake.community not in str(info.value)
    with pytest.raises(TypeError, match="cannot be pickled"):
        s.__getstate__()
    with pytest.raises(TypeError, match="not JSON serializable"):
        json.dumps({"community": s})
    assert fake.community not in json.dumps({"community": s}, default=str)


def test_copies_return_the_same_object(fake: FakeSecrets) -> None:
    """``copy`` and ``deepcopy`` keep the wrapper instead of failing or unwrapping.

    Args:
        fake: Fake secret values.

    Returns:
        None

    Raises:
        None
    """
    s = Secret(fake.community)
    assert copy.copy(s) is s
    assert copy.deepcopy({"k": s})["k"] is s
