"""Create a new subagents (domain agents) or core project from the bundled templates.

The subagents template ships a small example pack (order decisions: credit,
fulfillment, charges) so a new project starts green; replace it domain by domain.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

DEFAULT_KERNEL_SPEC = "rulegraph"


def _names(package: str) -> tuple[str, str, str]:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", package):
        raise ValueError(f"package must be a lowercase Python identifier, got {package!r}")
    return package, package.replace("_", "-"), package.replace("_", " ").title()


def scaffold(kind: str, dest: Path, package: str, *, agents_package: str | None = None,
             kernel_spec: str = DEFAULT_KERNEL_SPEC, agents_spec: str | None = None,
             amount_agent: str = "charges") -> list[Path]:
    if kind not in ("subagents", "core"):
        raise ValueError("kind must be 'subagents' or 'core'")
    pkg, dist, title = _names(package)
    subs = {"__PACKAGE__": pkg, "__DIST__": dist, "__TITLE__": title, "__KERNEL_SPEC__": kernel_spec}
    if kind == "core":
        if not agents_package:
            raise ValueError("core needs --agents-package (the installed subagents package)")
        apkg, adist, _ = _names(agents_package)
        subs.update({"__AGENTS_PACKAGE__": apkg, "__AGENTS_DIST__": adist,
                     "__AGENTS_SPEC__": agents_spec or adist, "__AMOUNT_AGENT__": amount_agent})
    root = Path(str(resources.files("rulegraph") / "templates" / kind))
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        existing = [p for p in dest.iterdir() if p.name not in (".git", "README.md", "LICENSE")]
        if existing:
            raise FileExistsError(f"{dest} is not empty: {', '.join(sorted(p.name for p in existing)[:5])}")
    written: list[Path] = []
    for src in sorted(root.rglob("*.tmpl")):
        rel = src.relative_to(root).as_posix()[: -len(".tmpl")].replace("dot-github", ".github")
        text = src.read_text()
        for k, v in subs.items():
            rel, text = rel.replace(k, v), text.replace(k, v)
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        written.append(out)
    return written
