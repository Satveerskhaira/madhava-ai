"""Field catalog: the closed vocabulary of record and reference fields.

Record fields are read from the record JSON by path. Reference fields carry a
JSON Pointer (RFC 6901) into the reference payload (plan setup, product terms,
fee schedule ...), with `{placeholders}` filled from `params`, which name other
catalog paths (record or reference, resolved first). Callers can therefore ask
for a handful of paths out of a payload of many thousands of fields.

Extra keys on an entry (for example `ncpdp: 401-D1` or `x12: CLP02`) are kept
in `extra` and exposed to search and tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

_MISSING = object()
_KNOWN = {"path", "source", "entity", "type", "concept", "description", "pointer", "params", "decoded_values"}


@dataclass
class FieldDef:
    path: str
    source: str                      # record | reference
    entity: str
    type: str
    concept: str
    description: str
    pointer: str | None = None
    params: dict[str, str] = field(default_factory=dict)
    decoded_values: dict[Any, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


class FieldCatalog:
    def __init__(self, fields: list[FieldDef]):
        self.fields = {f.path: f for f in fields}

    @classmethod
    def load(cls, path: Path) -> "FieldCatalog":
        return cls.from_entries(yaml.safe_load(path.read_text())["fields"])

    @classmethod
    def from_entries(cls, entries: list[dict[str, Any]]) -> "FieldCatalog":
        out = []
        for entry in entries:
            known = {k: v for k, v in entry.items() if k in _KNOWN}
            out.append(FieldDef(**known, extra={k: v for k, v in entry.items() if k not in _KNOWN}))
        return cls(out)

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
    def read_record(record: dict[str, Any], path: str) -> Any:
        """Read `<root>.a.b` from the record JSON (the root segment is dropped)."""
        node: Any = record
        for part in path.split(".")[1:]:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return _MISSING
        return node

    def resolve_pointer(self, path: str, record: dict[str, Any],
                        reference_value: Callable[[str], Any]) -> str | None:
        """Fill a reference field's pointer placeholders. None if a parameter is unavailable."""
        fdef = self.fields[path]
        pointer = fdef.pointer or ""
        for name, source in fdef.params.items():
            src = self.fields.get(source)
            value = self.read_record(record, source) if src is None or src.source == "record" else reference_value(source)
            if value is _MISSING or value is None:
                return None
            pointer = pointer.replace("{" + name + "}", str(value))
        return pointer

    @staticmethod
    def is_missing(value: Any) -> bool:
        return value is _MISSING
