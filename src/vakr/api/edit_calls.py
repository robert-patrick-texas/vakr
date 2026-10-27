"""Run a manual inventory edit and answer its refusals with field-level envelopes (META-008).

``edit_call`` runs a ``vakr.inventory.edits`` method in a worker thread,
like ``call``, and turns the two refusals that carry several fields into
the error envelope with one entry per field, so the web UI can show each
message beside its input:

- ``InventoryError`` (the file's field rules and checks): 422
  ``invalid_request``, fields ``body.<field>``;
- ``FileManagedError`` (META-008): 409 ``managed_by_file``, the message
  naming the managing file and ``vakr inventory apply``, fields
  ``body.<field>`` for each field the change would have touched.

Every other failure (404, a stale version's 409 ``conflict``, storage) goes
to the shared handlers unchanged.

Dependencies:
    vakr.api.context, errors: ``call`` and the envelope.
    vakr.inventory: The two refusals.

Safety:
    Messages are the services' own, which name fields and files, never values
    of a comment or a secret.

Example:
    >>> problems(InventoryError("device", [Issue("address", "bad")]))[0].field
    'body.address'
"""

from collections.abc import Callable

from vakr.api.context import call
from vakr.api.errors import ApiError, FieldProblem
from vakr.inventory.edits.ownership import FileManagedError
from vakr.inventory.errors import InventoryError, Issue

__all__ = ["Issue", "edit_call", "problems"]


def problems(exc: InventoryError) -> list[FieldProblem]:
    """Return an edit's problems as field problems under ``body``.

    Args:
        exc: The refusal.

    Returns:
        list[FieldProblem]: One per problem, in order.

    Raises:
        None
    """
    return [FieldProblem(f"body.{i.path}" if i.path else "body", i.reason) for i in exc.issues]


async def edit_call[T](function: Callable[..., T], *args: object, **kwargs: object) -> T:
    """Run an edit off the event loop and translate its field-level refusals.

    Args:
        function: A ``DeviceEditor`` or ``AnnotationEditor`` method.
        *args: Its positional arguments.
        **kwargs: Its keyword arguments.

    Returns:
        T: What it returned.

    Raises:
        ApiError: 422 or 409 with one field entry per refused field.
    """
    try:
        return await call(function, *args, **kwargs)
    except InventoryError as exc:
        found = problems(exc)
        first = f"{found[0].field.removeprefix('body.')}: {found[0].message}" if found else ""
        raise ApiError(422, "invalid_request", f"the change is not valid: {first}",
                       fields=found) from None  # fmt: skip
    except FileManagedError as exc:
        why = f"managed by {exc.source}"
        fields = [FieldProblem(f"body.{f}", why) for f in exc.fields]
        raise ApiError(409, "managed_by_file", str(exc), fields=fields) from None
