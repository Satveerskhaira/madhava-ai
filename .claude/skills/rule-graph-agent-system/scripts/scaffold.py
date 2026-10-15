#!/usr/bin/env python3
"""Scaffold a new rule-graph agent system from the madhava reference implementation.

Copies the working reference (package, knowledge, fixtures, goldens, tests, CI)
into a new project and renames the package, so the new project starts green
(validate, eval and tests pass) and is then adapted domain by domain.

Usage:
  python scaffold.py --dest ../claims-explainer --package claimsx
  python scaffold.py --dest ./out --package loanwhy --source /path/to/madhava-ai

--source defaults to the repository this script lives in (it walks up until it
finds src/madhava). From an account-installed copy of the skill, clone
https://github.com/Satveerskhaira/madhava-ai and pass --source.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

COPY = ["src/madhava", "knowledge", "fixtures", "goldens", "tests", "pyproject.toml", ".github/workflows/ci.yml"]
DOCS = {"docs/agent-system.md": "docs/reference-architecture.md"}
TEXT_SUFFIXES = {".py", ".yaml", ".yml", ".toml", ".md", ".json", ".txt"}
SKIP = {"__pycache__", ".pytest_cache"}


def find_source(start: Path) -> Path | None:
    for p in [start, *start.parents]:
        if (p / "src" / "madhava" / "agents" / "planner.py").exists():
            return p
    return None


def rename_text(text: str, package: str) -> str:
    title = "".join(part.capitalize() for part in re.split(r"[_-]", package))
    text = text.replace("MADHAVA", package.upper()).replace("Madhava", title)
    return re.sub(r"madhava(?!-ai)", package, text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", required=True, type=Path, help="new project directory (must not exist or be empty)")
    ap.add_argument("--package", required=True, help="python package name, e.g. claimsx")
    ap.add_argument("--source", type=Path, help="path to a madhava-ai checkout")
    args = ap.parse_args()

    if not re.fullmatch(r"[a-z][a-z0-9_]*", args.package):
        print("--package must be a lowercase python identifier", file=sys.stderr)
        return 2
    source = (args.source or find_source(Path(__file__).resolve()))
    if source is None or not (source / "src" / "madhava").exists():
        print("Reference not found. Clone https://github.com/Satveerskhaira/madhava-ai and pass --source.", file=sys.stderr)
        return 2
    dest: Path = args.dest.resolve()
    if dest.exists() and any(dest.iterdir()):
        print(f"{dest} is not empty", file=sys.stderr)
        return 2

    copied = 0
    for rel in COPY + list(DOCS):
        src = source / rel
        if not src.exists():
            continue
        target_rel = DOCS.get(rel, rel).replace("src/madhava", f"src/{args.package}")
        files = [src] if src.is_file() else [f for f in src.rglob("*") if f.is_file()]
        for f in files:
            if any(part in SKIP or part.endswith(".egg-info") for part in f.parts):
                continue
            inner = rename_text(str(f.relative_to(src)), args.package) if src.is_dir() else ""
            out = dest / target_rel / inner if src.is_dir() else dest / target_rel
            out.parent.mkdir(parents=True, exist_ok=True)
            if f.suffix in TEXT_SUFFIXES:
                out.write_text(rename_text(f.read_text(), args.package))
            else:
                shutil.copy2(f, out)
            copied += 1

    readme = dest / "README.md"
    readme.write_text(
        f"# {args.package}\n\n"
        "Planner and domain agents that explain why a transaction behaved the way it did, "
        "over a taxonomy, ontology and rules/policy graph.\n\n"
        "Scaffolded from the madhava reference implementation. The example knowledge, fixtures and goldens "
        "are pharmacy claims; replace them domain by domain, keeping `validate`, `eval` and `pytest` green.\n\n"
        "```bash\n"
        f"pip install -e \".[dev]\"\n{args.package} validate\n{args.package} --today 2026-10-14 eval\npytest\n"
        "```\n")
    print(f"Scaffolded {copied} files into {dest} as package '{args.package}'.")
    print("Next: pip install -e '.[dev]' && "
          f"{args.package} validate && {args.package} --today 2026-10-14 eval && pytest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
