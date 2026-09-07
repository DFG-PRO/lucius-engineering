from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any, TypeVar

from sqlalchemy.orm.attributes import flag_modified


JsonValue = TypeVar("JsonValue", dict[str, Any], list[Any])


def clone_json(value: JsonValue | None, default: JsonValue) -> JsonValue:
    return deepcopy(value if value is not None else default)


def set_json_field(row: object, field_name: str, value: JsonValue) -> JsonValue:
    durable_value = deepcopy(value)
    setattr(row, field_name, durable_value)
    flag_modified(row, field_name)
    return durable_value


def update_json_field(
    row: object,
    field_name: str,
    updater: Callable[[JsonValue], JsonValue | None],
    *,
    default: JsonValue,
) -> JsonValue:
    current = clone_json(getattr(row, field_name, None), default)
    updated = updater(current)
    return set_json_field(row, field_name, current if updated is None else updated)
