"""Shared base for the pharmacy claim domain agents.

Holds what every pharmacy domain shares: the base knowledge pack (ontology,
reject codes, top-level taxonomy with adjudication order, shared claim and
plan fields, procedures, wording), the claim adapter, the toolbox over the
claims and plan setup APIs, sample fixtures and the `pharmacy-domains` CLI.

Each domain agent ships in its own package as a knowledge fragment that
extends this base, registered under the `rulegraph.domains` entry point group.
"""

from .record import ClaimAdapter
from .system import (BASE_NAME, DomainSystem, base_source, build, build_toolbox, compose_knowledge,
                     discover_domains, fixtures_dir, pack_dir)

__version__ = "0.1.0"
__all__ = ["BASE_NAME", "ClaimAdapter", "DomainSystem", "base_source", "build", "build_toolbox", "compose_knowledge",
           "discover_domains", "fixtures_dir", "pack_dir"]
