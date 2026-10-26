"""rulegraph command line: validate, view and export knowledge packs, scaffold new projects.

    rulegraph --pack BASE [--fragment DIR ...] validate [--planner-procedures FILE]
    rulegraph --pack BASE [--fragment DIR ...] view [--out knowledge.html]
    rulegraph --pack BASE [--fragment DIR ...] export taxonomy|ontology|glossary|graph|shapes|graphml [--format ttl]
    rulegraph --pack BASE [--fragment DIR ...] shacl
    rulegraph --pack BASE [--fragment DIR ...] publish --uri mongodb://... --db knowledge
    rulegraph schemas --out DIR                       # JSON Schemas for editors and databases
    rulegraph new subagents --dest DIR --package NAME
    rulegraph new core --dest DIR --package NAME --agents-package NAME
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path


def _sources(args: argparse.Namespace):
    from .sources import DirectorySource
    if not args.pack:
        raise SystemExit("this command needs --pack <base pack dir> (and --fragment <dir> for each domain fragment)")
    return [DirectorySource(Path(args.pack)), *[DirectorySource(Path(f)) for f in args.fragment]]


def _knowledge(args: argparse.Namespace):
    from .knowledge import Knowledge
    procs = getattr(args, "planner_procedures", None)
    return Knowledge.compose(_sources(args), Path(procs) if procs else None)


def _validate(args: argparse.Namespace) -> int:
    from .rules.validate import validate
    kn = _knowledge(args)
    issues = validate(kn)
    for i in issues:
        print("ISSUE", i)
    print(f"{kn.origin}: {len(kn.rules.rules)} rule versions, {len(kn.rules.domains)} rule domains, "
          f"{len(kn.taxonomy.agents)} agents, {len(kn.catalog.fields)} catalog fields, "
          f"{len(kn.taxonomy.concepts)} concepts, {len(kn.glossary.terms) if kn.glossary else 0} glossary terms, "
          f"{len(kn.policies or {})} policy sections, graph {len(kn.graph.nodes)} nodes / "
          f"{kn.graph.edge_count} edges: {'OK' if not issues else f'{len(issues)} issues'}")
    return 1 if issues else 0


def _schemas(args: argparse.Namespace) -> int:
    from .schemas import NAMES, load
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        (out / f"{name}.schema.json").write_text(json.dumps(load(name), indent=2) + "\n")
    print(f"wrote {len(NAMES)} schemas to {out.resolve()}")
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
    p.add_argument("--pack", help="base pack directory")
    p.add_argument("--fragment", action="append", default=[], help="domain fragment directory (repeatable)")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="CI checks for a base pack and its fragments")
    v.add_argument("--planner-procedures")
    from .commands import add_knowledge_commands, run_knowledge_command
    add_knowledge_commands(sub)
    sc = sub.add_parser("schemas", help="write the JSON Schemas for knowledge files")
    sc.add_argument("--out", default="schemas")
    n = sub.add_parser("new", help="scaffold a subagents (domain agents) or core project")
    n.add_argument("kind", choices=["subagents", "core"])
    n.add_argument("--dest", required=True)
    n.add_argument("--package", required=True)
    n.add_argument("--agents-package", help="for core: the subagents package it uses")
    n.add_argument("--kernel-spec", default="rulegraph", help="pip requirement for the kernel, e.g. a git URL")
    n.add_argument("--agents-spec", help="for core: pip requirement for the subagents package")
    args = p.parse_args(argv)
    if args.cmd == "validate":
        return _validate(args)
    if args.cmd == "schemas":
        return _schemas(args)
    if args.cmd == "new":
        return _new(args)
    done = run_knowledge_command(args, lambda: _knowledge(args), lambda: _sources(args))
    return 2 if done is None else done


if __name__ == "__main__":
    sys.exit(main())
