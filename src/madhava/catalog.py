"""Field catalog: the closed vocabulary of claim and plan fields.

For plan fields it also knows how to read each path from the benefit setup
payload with a JSON Pointer, filling `{placeholders}` from claim fields or
other plan fields, so callers can ask for a handful of paths out of ~12k.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

_MISSING = object()


@dataclass
class FieldDef:
    path: str
    source: str
    entity: str
    type: str
    concept: str
    description: str
    pointer: str | None = None
    params: dict[str, str] = field(default_factory=dict)
    ncpdp: str | None = None
    decoded_values: dict[Any, str] = field(default_factory=dict)


class FieldCatalog:
    def __init__(self, fields: list[FieldDef]):
        self.fields = {f.path: f for f in fields}

    @classmethod
    def load(cls, path: Path) -> "FieldCatalog":
        data = yaml.safe_load(path.read_text())
        return cls([FieldDef(**f) for f in data["fields"]])

    def __contains__(self, path: str) -> bool:
        return path in self.fields

    def get(self, path: str) -> FieldDef:
        return self.fields[path]

    # --------------------------------------------------------------- reading
    @staticmethod
    def read_pointer(payload: Any, pointer: str) -> Any:
        node = payload
        for raw in pointer.lstrip("/").split("/"):
            key = raw.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict):
                if key in node:
                    node = node[key]
                else:
                    return _MISSING
            elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
                node = node[int(key)]
            else:
                return _MISSING
        return node

    @staticmethod
    def read_claim(claim: dict[str, Any], path: str) -> Any:
        node: Any = claim
        for part in path.split(".")[1:]:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return _MISSING
        return node

    def resolve_plan_pointer(self, path: str, claim: dict[str, Any],
                             plan_value: Callable[[str], Any]) -> str | None:
        """Fill a plan field's pointer placeholders. None if a parameter is unavailable."""
        fdef = self.fields[path]
        pointer = fdef.pointer or ""
        for name, source in fdef.params.items():
            if source.startswith("claim."):
                value = self.read_claim(claim, source)
            else:
                value = plan_value(source)
            if value is _MISSING or value is None:
                return None
            pointer = pointer.replace("{" + name + "}", str(value))
        return pointer

    @staticmethod
    def is_missing(value: Any) -> bool:
        return value is _MISSING
