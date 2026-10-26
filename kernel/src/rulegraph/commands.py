"""Knowledge commands shared by every CLI built on the kernel (rulegraph, a core agent, a domain package).

    view      write the offline knowledge browser (one HTML file) and optionally open it
    export    taxonomy | ontology | glossary (JSON-LD), graph (JSON-LD or Turtle), shapes (SHACL), graphml
    shacl     validate the exported graph against the SHACL shapes
    publish   write the packs to MongoDB and point a release at them
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path
from typing import Callable

from .knowledge import Knowledge
from .sources import KnowledgeSource


def add_knowledge_commands(sub: argparse._SubParsersAction) -> None:
    v = sub.add_parser("view", help="write the offline knowledge browser (HTML) and open it")
    v.add_argument("--out", default="knowledge.html")
    v.add_argument("--title")
    v.add_argument("--no-open", action="store_true", help="only write the file")
    e = sub.add_parser("export", help="export knowledge in standard formats")
    e.add_argument("what", choices=["taxonomy", "ontology", "glossary", "graph", "shapes", "graphml"])
    e.add_argument("--format", choices=["jsonld", "ttl"], default="jsonld", help="for graph: JSON-LD or Turtle")
    e.add_argument("--out", help="file to write (default: print)")
    sub.add_parser("shacl", help="validate the knowledge graph against its SHACL shapes (needs rulegraph[rdf])")
    pb = sub.add_parser("publish", help="publish the packs to MongoDB (needs rulegraph[mongo])")
    pb.add_argument("--uri", default="mongodb://localhost:27017")
    pb.add_argument("--db", default="knowledge")
    pb.add_argument("--release", default="current")
    pb.add_argument("--note", default="")


def _write(text: str, out: str | None) -> None:
    if out:
        Path(out).write_text(text)
        print(f"wrote {out} ({len(text):,} bytes)")
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def run_knowledge_command(args: argparse.Namespace, knowledge: Callable[[], Knowledge],
                          sources: Callable[[], list[KnowledgeSource]] | None = None) -> int | None:
    """Run view/export/shacl/publish if `args.cmd` is one of them; return None otherwise."""
    if args.cmd == "view":
        from .viewer import render_html
        out = Path(args.out).resolve()
        out.write_text(render_html(knowledge(), args.title))
        print(f"wrote {out}")
        if not args.no_open:
            webbrowser.open(out.as_uri())
        return 0
    if args.cmd == "export":
        kn = knowledge()
        if args.what == "taxonomy":
            _write(json.dumps(kn.taxonomy.to_jsonld(), indent=2), args.out)
        elif args.what == "ontology":
            _write(json.dumps(kn.ontology.to_jsonld(), indent=2), args.out)
        elif args.what == "glossary":
            _write(json.dumps(kn.glossary.to_jsonld() if kn.glossary else {}, indent=2), args.out)
        elif args.what == "graph":
            from .export import knowledge_graph, to_turtle
            doc = knowledge_graph(kn)
            _write(to_turtle(doc) if args.format == "ttl" else json.dumps(doc, indent=2), args.out)
        elif args.what == "shapes":
            from .export import shacl_shapes
            _write(shacl_shapes(kn), args.out)
        else:
            from .export import to_graphml
            _write(to_graphml(kn), args.out)
        return 0
    if args.cmd == "shacl":
        from .export import shacl_validate
        ok, report = shacl_validate(knowledge())
        print(report)
        return 0 if ok else 1
    if args.cmd == "publish":
        if sources is None:
            print("publish needs the separate pack sources")
            return 2
        from pymongo import MongoClient

        from .adapters.mongodb import ensure_indexes, publish
        db = MongoClient(args.uri)[args.db]
        ensure_indexes(db)
        result = publish(db, [s.load() for s in sources()], release=args.release, note=args.note)
        print(json.dumps(result, indent=2))
        return 0
    return None
