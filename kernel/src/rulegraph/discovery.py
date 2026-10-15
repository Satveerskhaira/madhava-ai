"""Find installed domain packages, so a new domain is a package install, not a core code change.

A domain package registers an entry point in the `rulegraph.domains` group that
points at a `DomainPackage` (or a function returning one):

    [project.entry-points."rulegraph.domains"]
    utilization_management = "pharmacy_um:package"

The core composes its base pack with every discovered fragment that extends it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Callable

from .sources import KnowledgeSource, PackDocuments

GROUP = "rulegraph.domains"


@dataclass
class DomainPackage:
    name: str                                 # distribution or fragment name
    source: KnowledgeSource                   # the fragment
    agents: list[str] = field(default_factory=list)   # domain agents it provides (top concepts' `agent`)
    version: str = ""

    def documents(self) -> PackDocuments:
        return self.source.load()


def discover(extends: str | None = None, group: str = GROUP) -> list[DomainPackage]:
    """Installed domain packages, optionally only those extending the given base pack name."""
    found: dict[str, DomainPackage] = {}
    for ep in entry_points(group=group):
        obj = ep.load()
        pkg = obj() if isinstance(obj, Callable) and not isinstance(obj, DomainPackage) else obj  # type: ignore[arg-type]
        if not isinstance(pkg, DomainPackage):
            raise TypeError(f"entry point {ep.name} ({ep.value}) is not a DomainPackage")
        if extends and pkg.documents().extends != extends:
            continue
        found[pkg.name] = pkg
    return [found[k] for k in sorted(found)]
