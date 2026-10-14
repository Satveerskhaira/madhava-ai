"""Clients for the claims API and the benefit (plan) setup API.

Fixture clients read the sample YAML under fixtures/. HTTP clients call real
services; set MADHAVA_CLAIMS_URL and MADHAVA_BENEFITS_URL and adapt the paths
in `HttpClaimsApi` / `HttpBenefitSetupApi` to your API contracts.
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
class PlanVersion:
    plan_id: str
    version: str
    effective_from: date
    effective_to: date | None
    payload: dict[str, Any]


class ClaimsApi(Protocol):
    def get_claim(self, claim_id: str) -> dict[str, Any]: ...


class BenefitSetupApi(Protocol):
    def get_plan_as_of(self, plan_id: str, as_of: date) -> PlanVersion: ...
    def list_versions(self, plan_id: str) -> list[tuple[str, date, date | None]]: ...


class NotFound(KeyError):
    pass


# ----------------------------------------------------------------- fixtures
class FixtureClaimsApi:
    def __init__(self, path: Path):
        self._claims: dict[str, dict[str, Any]] = yaml.safe_load(path.read_text())["claims"]

    def get_claim(self, claim_id: str) -> dict[str, Any]:
        if claim_id not in self._claims:
            raise NotFound(f"claim {claim_id} not found")
        return {"id": claim_id, **copy.deepcopy(self._claims[claim_id])}

    def claim_ids(self) -> list[str]:
        return sorted(self._claims)


def _deep_merge(base: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in changes.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _pad(payload: dict[str, Any], leaf_fields: int) -> dict[str, Any]:
    """Add filler sections so the payload is as large as the real API (~12k leaves)."""
    per_section = 100
    admin: dict[str, Any] = {}
    for s in range(leaf_fields // per_section):
        admin[f"section{s:03d}"] = {f"setting{f:03d}": f"value-{s}-{f}" for f in range(per_section)}
    return {**payload, "administration": admin}


def count_leaves(node: Any) -> int:
    if isinstance(node, dict):
        return sum(count_leaves(v) for v in node.values())
    if isinstance(node, list):
        return sum(count_leaves(v) for v in node) or 1
    return 1


class FixtureBenefitSetupApi:
    def __init__(self, directory: Path):
        self._plans: dict[str, list[PlanVersion]] = {}
        for f in sorted(directory.glob("*.yaml")):
            data = yaml.safe_load(f.read_text())
            versions: list[PlanVersion] = []
            payload: dict[str, Any] = {}
            raw = data["versions"]
            for i, v in enumerate(raw):
                payload = v["setup"] if "setup" in v else _deep_merge(payload, v.get("changes", {}))
                nxt = raw[i + 1]["effectiveFrom"] if i + 1 < len(raw) else None
                full = _pad(payload, data.get("synthetic_padding", 0))
                versions.append(PlanVersion(
                    plan_id=data["planId"], version=str(v["version"]),
                    effective_from=date.fromisoformat(str(v["effectiveFrom"])),
                    effective_to=date.fromisoformat(str(nxt)) if nxt else None,
                    payload={"planId": data["planId"], "version": str(v["version"]), **full}))
            self._plans[data["planId"]] = versions

    def get_plan_as_of(self, plan_id: str, as_of: date) -> PlanVersion:
        for v in reversed(self._plans.get(plan_id, [])):
            if v.effective_from <= as_of and (v.effective_to is None or as_of < v.effective_to):
                return v
        raise NotFound(f"no setup for plan {plan_id} on {as_of}")

    def list_versions(self, plan_id: str) -> list[tuple[str, date, date | None]]:
        return [(v.version, v.effective_from, v.effective_to) for v in self._plans.get(plan_id, [])]


# --------------------------------------------------------------------- HTTP
class HttpClaimsApi:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 5.0):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)

    def get_claim(self, claim_id: str) -> dict[str, Any]:
        r = self._client.get(f"/claims/{claim_id}")
        if r.status_code == 404:
            raise NotFound(claim_id)
        r.raise_for_status()
        return r.json()


class HttpBenefitSetupApi:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 5.0):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)

    def get_plan_as_of(self, plan_id: str, as_of: date) -> PlanVersion:
        r = self._client.get(f"/plans/{plan_id}/setup", params={"asOf": as_of.isoformat()})
        if r.status_code == 404:
            raise NotFound(plan_id)
        r.raise_for_status()
        body = r.json()
        return PlanVersion(plan_id, str(body["version"]), date.fromisoformat(body["effectiveFrom"]),
                           date.fromisoformat(body["effectiveTo"]) if body.get("effectiveTo") else None, body)

    def list_versions(self, plan_id: str) -> list[tuple[str, date, date | None]]:
        r = self._client.get(f"/plans/{plan_id}/versions")
        r.raise_for_status()
        return [(v["version"], date.fromisoformat(v["effectiveFrom"]),
                 date.fromisoformat(v["effectiveTo"]) if v.get("effectiveTo") else None) for v in r.json()]
