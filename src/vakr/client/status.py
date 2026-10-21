"""``SocketStatusSource``: the device list and device screen from ``vakr-api`` (13.1, TUI-007).

It satisfies ``vakr.query.StatusSource`` by asking the running API on its
local socket and decoding the answers into the very views ``StatusQueries``
returns in-process, so ``vakr status`` and ``vakr tui`` print and exit the
same way on either path (E.29 #10):

- ``device_list`` is ``GET /api/v1/devices`` with the request's search,
  filters, sort, and page (the offset travels as the API's cursor);
- ``device_detail`` is ``GET /api/v1/device-lookup?key=``, which looks the
  key up exactly as the in-process service does: display name first, then
  UUID.

Answers map back onto the in-process failures (``vakr.client.answers``):
404 on a lookup is "no such device", and every other failure is the
exception the local path would raise, or ``SourceUnavailableError``.

Dependencies:
    urllib.parse: Query strings.
    vakr.client.transport, answers: The bounded exchange and what answers mean.
    vakr.query: Views, requests, and ``from_jsonable``.
    vakr.domain.paging: The offset cursor codec shared with the API.

Safety:
    Read-only ``GET`` requests with no credentials.

Example:
    >>> list_query(DeviceListRequest(search="core"))
    [('sort', 'status'), ('limit', '100'), ('search', 'core')]
"""

from urllib.parse import urlencode

from vakr.client.answers import envelope, failure, parse_object
from vakr.client.transport import HINT, Limits, get
from vakr.domain.errors import ValidationError
from vakr.domain.paging import MAX_OFFSET, offset_cursor
from vakr.query.detail_views import DeviceDetailView
from vakr.query.from_json import ContractError, from_jsonable
from vakr.query.listing import DeviceListRequest
from vakr.query.source import MAX_DEVICE_KEY_LENGTH, SourceUnavailableError
from vakr.query.views import DeviceListView

DEVICES = "/api/v1/devices"
LOOKUP = "/api/v1/device-lookup"
LIVE = "/api/v1/health/live"


def list_query(request: DeviceListRequest) -> list[tuple[str, str]]:
    """Return the query parameters of ``GET /api/v1/devices`` for a request.

    Args:
        request: The in-process request.

    Returns:
        list[tuple[str, str]]: Parameters in a fixed order; sets are sorted.

    Raises:
        None
    """
    query = [("sort", request.sort.value), ("limit", str(request.limit))]
    if request.search:
        query.append(("search", request.search))
    query += [("bucket", b.value) for b in sorted(request.buckets, key=lambda b: b.value)]
    if request.include_retired:
        query.append(("include_retired", "true"))
    query += [("freshness", f.value) for f in sorted(request.freshness, key=lambda f: f.value)]
    if request.collector is not None:
        query.append(("collector", str(request.collector)))
    if request.offset and (cursor := offset_cursor(request.offset)) is not None:
        query.append(("cursor", cursor))
    return query


class SocketStatusSource:
    """``StatusSource`` over the ``vakr-api`` socket.

    Attributes:
        socket_path: The socket file.
    """

    def __init__(self, socket_path: str, limits: Limits | None = None) -> None:
        """Bind the source to a socket; nothing is opened until a read.

        Args:
            socket_path: The socket file (``api.socket_path``).
            limits: Exchange bounds; Appendix E.29's when ``None``.

        Returns:
            None

        Raises:
            None
        """
        self.socket_path = str(socket_path)
        self._limits = limits or Limits()

    def check(self) -> None:
        """Make sure the API answers and serves this account, before a screen opens (13.4).

        Args:
            None

        Returns:
            None

        Raises:
            SourceUnavailableError: If the API cannot be used.
        """
        status, body = self._get(LIVE, [])
        self._ok(status, body)

    def device_list(self, request: DeviceListRequest | None = None) -> DeviceListView:
        """Return one page of the device list from the API.

        Args:
            request: Search, filter, sort, and page; the defaults when ``None``.

        Returns:
            DeviceListView: The page.

        Raises:
            SourceUnavailableError: If the API cannot be used.
            StorageError: If the API cannot read inventory.
            ValidationError: If the offset is beyond the API's cursor bound, or the API refuses.
        """
        request = request or DeviceListRequest()
        if request.offset > MAX_OFFSET:
            raise ValidationError(
                f"offset is limited to {MAX_OFFSET} over vakr-api", field="offset"
            )
        status, body = self._get(DEVICES, list_query(request))
        return self._view(DeviceListView, self._ok(status, body))

    def device_detail(self, key: str) -> DeviceDetailView | None:
        """Return one device's screen from the API.

        Args:
            key: Exact display name, else device UUID.

        Returns:
            DeviceDetailView | None: ``None`` when nothing matches, or when the
            key is empty or longer than ``MAX_DEVICE_KEY_LENGTH``.

        Raises:
            SourceUnavailableError: If the API cannot be used.
            StorageError: If the API cannot read inventory.
        """
        if not key or len(key) > MAX_DEVICE_KEY_LENGTH:
            return None
        status, body = self._get(LOOKUP, [("key", key)])
        if status == 404 and envelope(body)[0] == "not_found":
            return None
        return self._view(DeviceDetailView, self._ok(status, body))

    def _get(self, path: str, query: list[tuple[str, str]]) -> tuple[int, dict[str, object] | None]:
        """Send one ``GET``.

        Args:
            path: The endpoint.
            query: Its parameters.

        Returns:
            tuple: HTTP status and the body's object (``None`` when it is not one).

        Raises:
            SourceUnavailableError: If the exchange fails (``vakr.client.transport``).
        """
        target = f"{path}?{urlencode(query)}" if query else path
        answer = get(self.socket_path, target, self._limits)
        return answer.status, parse_object(answer.body)

    def _ok(self, status: int, body: dict[str, object] | None) -> dict[str, object]:
        """Return a successful answer's object, or raise what the failure means.

        Args:
            status: HTTP status.
            body: The parsed body.

        Returns:
            dict: The object of a 200 answer.

        Raises:
            SourceUnavailableError, StorageError, ValidationError: As ``failure`` maps them.
        """
        if status == 200 and body is not None:
            return body
        error = envelope(body) if status != 200 else ("", "the answer is not a JSON object", "")
        raise failure(status, error, self.socket_path)

    def _view[T](self, kind: type[T], body: dict[str, object]) -> T:
        """Decode an answer into its view.

        Args:
            kind: The view type.
            body: The parsed answer.

        Returns:
            T: The view.

        Raises:
            SourceUnavailableError: If the answer does not fit the view.
        """
        try:
            return from_jsonable(kind, body)
        except ContractError as exc:
            raise SourceUnavailableError(
                f"vakr-api's answer does not fit this release's views ({exc}); after an update, "
                f"`sudo systemctl restart vakr-api` so both run the same release; {HINT}"
            ) from None
