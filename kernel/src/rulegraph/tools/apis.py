"""Clients for the record API and the reference-data API.

- Record API: one record by id (a claim, an application, a payment ...).
- Reference API: the effective-dated reference data rules read (plan setup,
  product terms, fee schedules ...), resolved as of a date.

Fixture clients read YAML so packs can ship sample data and tests. HTTP
clients take URL templates so any service shape can be used.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import httpx
import yaml


@dataclass
class ReferenceVersion:
    reference_id: str
    version: str
    effective_from: date
    effective_to: date | None
    payload: dict[str, Any]


class RecordApi(Protocol):
    def get_record(self, record_id: str) -> dict[str, Any]: ...


class ReferenceApi(Protocol):
    def get_as_of(self, reference_id: str, as_of: date) -> ReferenceVersion: ...
    def list_versions(self, reference_id: str) -> list[tuple[str, date, date | None]]: ...


class NotFound(KeyError):
    pass


# ----------------------------------------------------------------- fixtures
class FixtureRecordApi:
    """Records from a YAML file: {<key>: {<id>: {...}}}."""

    def __init__(self, path: Path, key: str = "records"):
        data = yaml.safe_load(path.read_text())
        self._records: dict[str, dict[str, Any]] = data[key]

    def get_record(self, record_id: str) -> dict[str, Any]:
        if record_id not in self._records:
            raise NotFound(f"record {record_id} not found")
        return {"id": record_id, **copy.deepcopy(self._records[record_id])}

    def record_ids(self) -> list[str]:
        return sorted(self._records)


def deep_merge(base: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in changes.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _pad(payload: dict[str, Any], leaf_fields: int) -> dict[str, Any]:
    """Filler sections so a sample payload is as large as the real API."""
    per_section = 100
    admin: dict[str, Any] = {}
    for s in range(leaf_fields // per_section):
        admin[f"section{s:03d}"] = {f"setting{f:03d}": f"value-{s}-{f}" for f in range(per_section)}
    return {**payload, "administration": admin} if admin else payload


def count_leaves(node: Any) -> int:
    if isinstance(node, dict):
        return sum(count_leaves(v) for v in node.values())
    if isinstance(node, list):
        return sum(count_leaves(v) for v in node) or 1
    return 1


class FixtureReferenceApi:
    """Reference data from YAML files in a directory, one file per reference id.

    Each file: {id: ..., synthetic_padding: n, versions: [{version, effectiveFrom, setup | changes}]}.
    Later versions list only what changed and are deep-merged over the previous one.
    """

    def __init__(self, directory: Path):
        self._refs: dict[str, list[ReferenceVersion]] = {}
        for f in sorted(directory.glob("*.yaml")):
            data = yaml.safe_load(f.read_text())
            ref_id = str(data.get("id") or data.get("planId"))
            versions: list[ReferenceVersion] = []
            payload: dict[str, Any] = {}
            raw = data["versions"]
            for i, v in enumerate(raw):
                payload = v["setup"] if "setup" in v else deep_merge(payload, v.get("changes", {}))
                nxt = raw[i + 1]["effectiveFrom"] if i + 1 < len(raw) else None
                full = _pad(payload, data.get("synthetic_padding", 0))
                versions.append(ReferenceVersion(
                    reference_id=ref_id, version=str(v["version"]),
                    effective_from=date.fromisoformat(str(v["effectiveFrom"])),
                    effective_to=date.fromisoformat(str(nxt)) if nxt else None,
                    payload={"id": ref_id, "version": str(v["version"]), **full}))
            self._refs[ref_id] = versions

    def get_as_of(self, reference_id: str, as_of: date) -> ReferenceVersion:
        for v in reversed(self._refs.get(reference_id, [])):
            if v.effective_from <= as_of and (v.effective_to is None or as_of < v.effective_to):
                return v
        raise NotFound(f"no reference data for {reference_id} on {as_of}")

    def list_versions(self, reference_id: str) -> list[tuple[str, date, date | None]]:
        return [(v.version, v.effective_from, v.effective_to) for v in self._refs.get(reference_id, [])]


# --------------------------------------------------------------------- HTTP
class HttpRecordApi:
    def __init__(self, base_url: str, path_template: str = "/records/{id}", token: str | None = None,
                 timeout: float = 5.0):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)
        self._path = path_template

    def get_record(self, record_id: str) -> dict[str, Any]:
        r = self._client.get(self._path.format(id=record_id))
        if r.status_code == 404:
            raise NotFound(record_id)
        r.raise_for_status()
        return r.json()


class HttpReferenceApi:
    """Expects `{as_of_path}` to return {version, effectiveFrom, effectiveTo?, ...payload}."""

    def __init__(self, base_url: str, as_of_path: str = "/references/{id}?asOf={date}",
                 versions_path: str = "/references/{id}/versions", token: str | None = None, timeout: float = 5.0):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)
        self._as_of, self._versions = as_of_path, versions_path

    def get_as_of(self, reference_id: str, as_of: date) -> ReferenceVersion:
        r = self._client.get(self._as_of.format(id=reference_id, date=as_of.isoformat()))
        if r.status_code == 404:
            raise NotFound(reference_id)
        r.raise_for_status()
        body = r.json()
        return ReferenceVersion(reference_id, str(body["version"]), date.fromisoformat(body["effectiveFrom"]),
                                date.fromisoformat(body["effectiveTo"]) if body.get("effectiveTo") else None, body)

    def list_versions(self, reference_id: str) -> list[tuple[str, date, date | None]]:
        r = self._client.get(self._versions.format(id=reference_id))
        r.raise_for_status()
        return [(v["version"], date.fromisoformat(v["effectiveFrom"]),
                 date.fromisoformat(v["effectiveTo"]) if v.get("effectiveTo") else None) for v in r.json()]
