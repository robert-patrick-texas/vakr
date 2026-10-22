"""Dashboard widgets to and from plain JSON values, with widget-schema upgrades (DASH-007).

Widgets are stored as a JSON array beside the ``widget_schema`` version
that wrote them, and the API takes the same shape in request bodies, so
one decoder checks both. Schema 1:

- ``{"kind": "pinned_interfaces", "title", "interfaces": [uuid, ...], "graphs"}``
- ``{"kind": "interface_selection", "title", "criteria": {...}, "limit"}``
- ``{"kind": "device_selection", "title", "criteria": {...}, "limit"}``
- ``{"kind": "ranking", "title", "ranking", "criteria": {...}, "window", "limit"}``

Interface criteria are ``site_id``, ``tag_id``, ``group_id``, ``provider``,
``wan_only``, and ``interest``; device criteria the first three. Absent
keys take their defaults; unknown keys are refused, so a typo never
silently drops a filter.

**Upgrades.** Decoding first brings older widget JSON to the current
schema through ``vakr.domain.dashboard_upgrades`` (DASH-007), so the
rules below only ever see the current shape.

Dependencies:
    collections.abc: Mappings.
    vakr.domain.dashboard_upgrades, dashboard_widgets, enums, errors, ids.

Safety:
    Error messages name the field path (``widgets[2].criteria.site_id``).
    Only a rejected UUID is echoed, shortened, as ``parse_uuid`` does
    everywhere; no widget field holds a secret.

Example:
    >>> widgets = widgets_from_json(1, [{"kind": "device_selection", "title": "Core",
    ...     "criteria": {"site_id": "0190a3c4-0000-7000-8000-000000000001"}}])
    >>> widgets_to_json(widgets)[0]["limit"]
    25
"""

from collections.abc import Mapping
from typing import Any

from vakr.domain.dashboard_upgrades import upgrade
from vakr.domain.dashboard_widgets import (
    DeviceCriteria,
    DeviceSelection,
    InterfaceCriteria,
    InterfaceSelection,
    PinnedInterfaces,
    RankingKind,
    RankingWidget,
    Widget,
    WidgetKind,
)
from vakr.domain.enums import InterestClass
from vakr.domain.errors import ValidationError
from vakr.domain.ids import GroupId, InterfaceId, SiteId, TagId, parse_uuid

type Json = Any
_ID_KEYS = {"site_id": SiteId, "tag_id": TagId, "group_id": GroupId}
_INTERFACE_KEYS = frozenset({*_ID_KEYS, "provider", "wan_only", "interest"})
_WIDGET_KEYS = {
    WidgetKind.PINNED_INTERFACES: frozenset({"kind", "title", "interfaces", "graphs"}),
    WidgetKind.INTERFACE_SELECTION: frozenset({"kind", "title", "criteria", "limit"}),
    WidgetKind.DEVICE_SELECTION: frozenset({"kind", "title", "criteria", "limit"}),
    WidgetKind.RANKING: frozenset({"kind", "title", "ranking", "criteria", "window", "limit"}),
}


def _object(value: Json, where: str, allowed: frozenset[str]) -> dict[str, Any]:
    """Require a JSON object with only known keys.

    Args:
        value: Candidate object.
        where: Field path for messages.
        allowed: Keys it may have.

    Returns:
        dict[str, Any]: The object; its values are checked by the records built from it.

    Raises:
        ValidationError: If it is not an object or has an unknown key.
    """
    if not isinstance(value, Mapping):
        raise ValidationError(f"{where} must be an object", field=where)
    unknown = sorted(str(k) for k in value if k not in allowed)
    if unknown:
        raise ValidationError(f"{where} has unknown field {unknown[0][:40]!r}", field=where)
    return dict(value)


def _ids(value: Json, where: str) -> dict[str, Any]:
    """Decode the UUID filters of a criteria object.

    Args:
        value: The criteria object.
        where: Field path for messages.

    Returns:
        dict[str, Any]: ``site_id``, ``tag_id``, and ``group_id`` as typed IDs or ``None``.

    Raises:
        ValidationError: If one is not a canonical UUID.
    """
    out: dict[str, Any] = {}
    for key, kind in _ID_KEYS.items():
        text = value.get(key)
        out[key] = None if text is None else kind(parse_uuid(text, f"{where}.{key}"))
    return out


def _interface_criteria(value: Json, where: str) -> InterfaceCriteria:
    """Decode interface criteria.

    Args:
        value: The criteria object, or ``None`` for no filter.
        where: Field path for messages.

    Returns:
        InterfaceCriteria: The criteria.

    Raises:
        ValidationError: If a field is invalid.
    """
    obj = _object({} if value is None else value, where, _INTERFACE_KEYS)
    interest = obj.get("interest")
    if interest is not None and interest not in {c.value for c in InterestClass}:
        raise ValidationError(f"{where}.interest must be primary or secondary", field=where)
    return InterfaceCriteria(
        **_ids(obj, where),
        provider=obj.get("provider"),
        wan_only=obj.get("wan_only", False),
        interest=None if interest is None else InterestClass(interest),
    )


def _widget(value: Json, where: str) -> Widget:
    """Decode one widget of the current schema.

    Args:
        value: The widget object.
        where: Field path for messages, such as ``widgets[0]``.

    Returns:
        Widget: The widget.

    Raises:
        ValidationError: If the kind is unknown or a field is invalid.
    """
    kinds = {k.value for k in WidgetKind}
    kind = value.get("kind") if isinstance(value, Mapping) else None
    if kind not in kinds:
        raise ValidationError(f"{where}.kind must be one of {', '.join(sorted(kinds))}",
                              field=f"{where}.kind")  # fmt: skip
    obj = _object(value, where, _WIDGET_KEYS[WidgetKind(kind)])
    title: Any = obj.get("title")  # any JSON value; the widget's own check refuses a non-name
    match WidgetKind(kind):
        case WidgetKind.PINNED_INTERFACES:
            raw = obj.get("interfaces")
            if not isinstance(raw, list):
                raise ValidationError(f"{where}.interfaces must be an array",
                                      field=f"{where}.interfaces")  # fmt: skip
            ids = tuple(InterfaceId(parse_uuid(t, f"{where}.interfaces")) for t in raw)
            return PinnedInterfaces(title, ids, obj.get("graphs", True))
        case WidgetKind.INTERFACE_SELECTION:
            criteria = _interface_criteria(obj.get("criteria"), f"{where}.criteria")
            return InterfaceSelection(title, criteria, obj.get("limit", 25))
        case WidgetKind.DEVICE_SELECTION:
            crit = _object(obj.get("criteria") or {}, f"{where}.criteria", frozenset(_ID_KEYS))
            device = DeviceCriteria(**_ids(crit, f"{where}.criteria"))
            return DeviceSelection(title, device, obj.get("limit", 25))
        case WidgetKind.RANKING:
            ranking = obj.get("ranking")
            if ranking not in {r.value for r in RankingKind}:
                raise ValidationError(f"{where}.ranking must be utilization or errors",
                                      field=f"{where}.ranking")  # fmt: skip
            criteria = _interface_criteria(obj.get("criteria"), f"{where}.criteria")
            return RankingWidget(title, RankingKind(ranking), criteria,
                                 obj.get("window", "15m"), obj.get("limit", 10))  # fmt: skip


def _located(exc: ValidationError, where: str) -> ValidationError:
    """Return the error with its field path under ``where``.

    Args:
        exc: The error a widget raised.
        where: The widget's path, such as ``widgets[2]``.

    Returns:
        ValidationError: The same message and field, both prefixed with the path.

    Raises:
        None
    """
    field = exc.field or ""
    if not field.startswith(where):
        field = f"{where}.{field}" if field else where
    text = str(exc) if str(exc).startswith(where) else f"{where}: {exc}"
    return ValidationError(text, field=field)


def widgets_from_json(schema: int, value: Json) -> tuple[Widget, ...]:
    """Decode a widget array, upgrading it first when it is older (DASH-007).

    Args:
        schema: The widget schema that wrote ``value``.
        value: A JSON array of widget objects.

    Returns:
        tuple[Widget, ...]: The widgets, in order.

    Raises:
        ValidationError: If the array or a widget is invalid; ``field`` names its path.
    """
    if not isinstance(value, list):
        raise ValidationError("widgets must be an array", field="widgets")
    current = upgrade(schema, value)
    out: list[Widget] = []
    for n, item in enumerate(current):
        where = f"widgets[{n}]"
        try:
            out.append(_widget(item, where))
        except ValidationError as exc:
            raise _located(exc, where) from None
    return tuple(out)


def _criteria_json(criteria: InterfaceCriteria | DeviceCriteria) -> dict[str, Json]:
    """Encode criteria, leaving out filters that are not set.

    Args:
        criteria: Interface or device criteria.

    Returns:
        dict[str, Json]: The object.

    Raises:
        None
    """
    out: dict[str, Json] = {k: str(getattr(criteria, k)) for k in _ID_KEYS
                            if getattr(criteria, k) is not None}  # fmt: skip
    if isinstance(criteria, InterfaceCriteria):
        if criteria.provider is not None:
            out["provider"] = criteria.provider
        if criteria.wan_only:
            out["wan_only"] = True
        if criteria.interest is not None:
            out["interest"] = criteria.interest.value
    return out


def widgets_to_json(widgets: tuple[Widget, ...]) -> list[Json]:
    """Encode widgets as schema-``WIDGET_SCHEMA`` JSON values.

    Args:
        widgets: The widgets.

    Returns:
        list[Json]: One object per widget, every field explicit.

    Raises:
        None
    """
    out: list[Json] = []
    for w in widgets:
        base: dict[str, Json] = {"kind": w.kind.value, "title": w.title}
        match w:
            case PinnedInterfaces():
                base |= {"interfaces": [str(i) for i in w.interfaces], "graphs": w.graphs}
            case InterfaceSelection() | DeviceSelection():
                base |= {"criteria": _criteria_json(w.criteria), "limit": w.limit}
            case RankingWidget():
                base |= {"ranking": w.ranking.value, "criteria": _criteria_json(w.criteria),
                         "window": w.window, "limit": w.limit}  # fmt: skip
        out.append(base)
    return out
