"""rulegraph command line: validate and export knowledge packs, scaffold new projects.

    rulegraph validate <pack_dir> [--planner-procedures FILE]
    rulegraph export taxonomy|ontology <pack_dir>
    rulegraph new subagents --dest DIR --package NAME
    rulegraph new core --dest DIR --package NAME --agents-package NAME
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path


def _validate(args: argparse.Namespace) -> int:
    from .knowledge import Knowledge
    from .rules.validate import validate
    kn = Knowledge.load(Path(args.pack), Path(args.planner_procedures) if args.planner_procedures else None)
    issues = validate(kn)
    for i in issues:
        print("ISSUE", i)
    print(f"{kn.pack.name}: {len(kn.rules.rules)} rule versions, {len(kn.rules.domains)} rule domains, "
          f"{len(kn.taxonomy.agents)} agents, {len(kn.catalog.fields)} catalog fields, "
          f"{len(kn.taxonomy.concepts)} concepts, policy graph {len(kn.graph.nodes)} nodes / "
          f"{kn.graph.edge_count} edges: {'OK' if not issues else f'{len(issues)} issues'}")
    return 1 if issues else 0


def _export(args: argparse.Namespace) -> int:
    from .knowledge import Knowledge
    kn = Knowledge.load(Path(args.pack))
    doc = kn.taxonomy.to_jsonld() if args.what == "taxonomy" else kn.ontology.to_jsonld()
    print(json.dumps(doc, indent=2))
    return 0


def _new(args: argparse.Namespace) -> int:
    from .scaffold import scaffold
    files = scaffold(args.kind, Path(args.dest), args.package, agents_package=args.agents_package,
                     kernel_spec=args.kernel_spec, agents_spec=args.agents_spec)
    print(f"Created {len(files)} files in {Path(args.dest).resolve()} ({args.kind}, package '{args.package}').")
    print("Next: pip install -e '.[dev]' && pytest")
    return 0


def main(argv: list[str] | None = None) -> int:
    warnings.filterwarnings("ignore")
    p = argparse.ArgumentParser(prog="rulegraph", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="CI checks for a knowledge pack")
    v.add_argument("pack")
    v.add_argument("--planner-procedures")
    e = sub.add_parser("export", help="export taxonomy (SKOS) or ontology (OWL) as JSON-LD")
    e.add_argument("what", choices=["taxonomy", "ontology"])
    e.add_argument("pack")
    n = sub.add_parser("new", help="scaffold a subagents (domain agents) or core project")
    n.add_argument("kind", choices=["subagents", "core"])
    n.add_argument("--dest", required=True)
    n.add_argument("--package", required=True)
    n.add_argument("--agents-package", help="for core: the subagents package it uses")
    n.add_argument("--kernel-spec", default="rulegraph", help="pip requirement for the kernel, e.g. a git URL")
    n.add_argument("--agents-spec", help="for core: pip requirement for the subagents package")
    args = p.parse_args(argv)
    return {"validate": _validate, "export": _export, "new": _new}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
