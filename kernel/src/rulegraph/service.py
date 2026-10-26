"""The knowledge service: a small read-only HTTP API over one or more knowledge releases, plus the live browser.

    rulegraph --pack base --fragment um knowledge-service             # serve the files on disk
    madhava knowledge-service --mongo mongodb://localhost:27017       # serve what is published in MongoDB

This is the read side of the application. Authors change YAML in Git, CI validates and `publish` writes it to
MongoDB; this service loads a release through the kernel (the same composition the agents use), derives the graph,
outcomes and cross references in memory, and serves them. Nothing derived is stored.

Endpoints (all GET, JSON unless noted):

    /                                   the knowledge browser, reading this API (live mode)
    /api/health
    /api/releases                       every release and the packs it points at
    /api/releases/{r}/view              the whole view model the browser renders (ETag per publish)
    /api/releases/{r}/meta | concepts | terms | ontology | rules | rulesets | fields | policies | outcomes
    /api/releases/{r}/rules/{id}        one rule with every version, rule sets and the policy text it cites
    /api/releases/{r}/graph?agent=&focus=&depth=   the whole graph, one agent's sector, or a neighbourhood
    /api/releases/{r}/search?q=         terms, concepts, rules, fields, policies, outcomes, rule sets
    /api/releases/{r}/export/{name}     graph.jsonld | graph.ttl | taxonomy.jsonld | glossary.jsonld |
                                        ontology.jsonld | shapes.ttl | knowledge.graphml

The standard library serves it, so there is nothing to add to a deployment; behind a gateway it can equally be
mounted in FastAPI or Spring by calling `KnowledgeService` methods.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from .knowledge import Knowledge

FILES = "files"     # release name used when serving the pack files directly


class NotFound(LookupError):
    pass


class KnowledgeService:
    """Loads releases on demand, caches the derived view per publish, and answers the API calls.

    `files` serves one composed knowledge set read from disk (release name "files").
    `db` serves every release published to MongoDB; a release is reloaded when its `published_at` changes,
    so a new publish shows up on the next request without restarting anything.
    """

    def __init__(self, *, files: Callable[[], Knowledge] | None = None, db: Any = None,
                 default_release: str = "current", title: str | None = None):
        if (files is None) == (db is None):
            raise ValueError("give exactly one of files= or db=")
        self.files, self.db, self.title = files, db, title
        self.default_release = FILES if files else default_release
        self._cache: dict[str, tuple[str, Knowledge, dict[str, Any], str]] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- releases
    def releases(self) -> list[dict[str, Any]]:
        if self.files:
            return [{"release": FILES, "source": "files", "packs": None, "default": True}]
        out = []
        for r in self.db["releases"].find({}).sort("_id", 1):
            out.append({"release": r["_id"], "source": "mongodb", "packs": r.get("packs", []), "note": r.get("note"),
                        "publishedAt": r.get("published_at"), "default": r["_id"] == self.default_release})
        return out

    def _stamp(self, release: str) -> tuple[str, dict[str, Any] | None]:
        if self.files:
            if release != FILES:
                raise NotFound(f"release {release!r} (serving files: the only release is {FILES!r})")
            return "files", None
        head = self.db["releases"].find_one({"_id": release})
        if head is None:
            raise NotFound(f"release {release!r} is not published")
        return str(head.get("published_at")), head

    def load(self, release: str) -> tuple[Knowledge, dict[str, Any], str]:
        """(knowledge, view model, etag) for a release; rebuilt only when the release was republished."""
        stamp, head = self._stamp(release)
        with self._lock:
            hit = self._cache.get(release)
            if hit and hit[0] == stamp:
                return hit[1], hit[2], hit[3]
            from .viewer import view_data
            if self.files:
                kn = self.files()
            else:
                from .adapters.mongodb import mongo_sources
                kn = Knowledge.compose(mongo_sources(self.db, release))
            view = view_data(kn, self.title)
            view["meta"].update({"release": release, "source": "files" if self.files else "mongodb",
                                 "publishedAt": head.get("published_at") if head else None,
                                 "packs": head.get("packs") if head else None})
            etag = '"' + hashlib.sha1(f"{release}|{stamp}".encode()).hexdigest()[:16] + '"'
            self._cache[release] = (stamp, kn, view, etag)
            return kn, view, etag

    # ---------------------------------------------------------------- read model
    def section(self, release: str, name: str) -> Any:
        _, view, _ = self.load(release)
        if name not in {"meta", "concepts", "terms", "ontology", "rules", "rulesets", "fields", "policies", "outcomes"}:
            raise NotFound(f"no section {name!r}")
        return view[name]

    def rule(self, release: str, rule_id: str) -> dict[str, Any]:
        _, view, _ = self.load(release)
        rule = next((r for r in view["rules"] if r["id"] == rule_id), None)
        if rule is None:
            raise NotFound(f"no rule {rule_id!r}")
        pol = {p["key"]: p for p in view["policies"]}
        sets = {s["id"]: s for s in view["rulesets"]}
        return {**rule,
                "policyText": {k: pol[k] for v in rule["versions"] for k in v["sources"] if k in pol},
                "ruleSets": {k: sets[k] for v in rule["versions"] for k in v["rulesets"] if k in sets}}

    def graph(self, release: str, agent: str | None = None, focus: str | None = None, depth: int = 1) -> dict[str, Any]:
        """The whole graph, one domain agent's sector (with the shared nodes it touches) or a neighbourhood."""
        _, view, _ = self.load(release)
        g = view["graph"]
        nodes, edges = g["nodes"], g["edges"]
        keep: set[str] | None = None
        if agent:
            own = {n["id"] for n in nodes if n["group"] == agent}
            if not own:
                raise NotFound(f"no agent {agent!r}")
            keep = own | {d for s, _, d in edges if s in own} | {s for s, _, d in edges if d in own}
        if focus:
            if focus not in {n["id"] for n in nodes}:
                raise NotFound(f"no node {focus!r}")
            adj: dict[str, set[str]] = {}
            for s, _, d in edges:
                adj.setdefault(s, set()).add(d)
                adj.setdefault(d, set()).add(s)
            seen, q = {focus}, deque([(focus, 0)])
            while q:
                n, k = q.popleft()
                if k == depth:
                    continue
                for m in adj.get(n, ()):
                    if m not in seen:
                        seen.add(m)
                        q.append((m, k + 1))
            keep = seen if keep is None else keep & seen
        if keep is None:
            return g
        return {"nodes": [n for n in nodes if n["id"] in keep],
                "edges": [e for e in edges if e[0] in keep and e[2] in keep], "groups": g["groups"]}

    def search(self, release: str, q: str, limit: int = 25) -> list[dict[str, str]]:
        _, view, _ = self.load(release)
        words = [w for w in q.lower().split() if w]
        if not words:
            return []
        items = [("term", t["id"], t["label"], [t["label"], *t["alt"], t["definition"]]) for t in view["terms"]]
        items += [("concept", c["id"], c["label"], [c["id"], c["label"], *c["alt"], c["definition"] or ""])
                  for c in view["concepts"]]
        items += [("rule", r["id"], r["id"], [r["id"], r["domain"], r["versions"][-1]["intent"] or ""]) for r in view["rules"]]
        items += [("field", f["path"], f["path"], [f["path"], f["description"] or ""]) for f in view["fields"]]
        items += [("policy", p["key"], p["title"] or p["key"], [p["key"], p["title"] or "", p["text"] or ""])
                  for p in view["policies"]]
        items += [("outcome", o["token"], o["token"], [o["token"], o["meaning"] or ""]) for o in view["outcomes"]]
        items += [("ruleset", s["id"], s["id"], [s["id"], s["domain"], s["reason"] or ""]) for s in view["rulesets"]]
        hits = []
        whole = q.lower().strip()
        for kind, ident, label, text in items:
            blob = " ".join(text).lower()
            if all(w in blob for w in words):
                lab = label.lower()
                score = (3 if lab.startswith(whole) else 0) + (2 if whole in lab else 0) + (0.5 if kind == "term" else 0)
                hits.append((score, {"kind": kind, "id": ident, "label": label, "node": f"{kind}:{ident}"}))
        hits.sort(key=lambda h: -h[0])
        return [h for _, h in hits[:limit]]

    def export(self, release: str, name: str) -> tuple[str, str]:
        kn, _, _ = self.load(release)
        from . import export as ex
        if name == "graph.jsonld":
            return json.dumps(ex.knowledge_graph(kn), indent=2), "application/ld+json"
        if name == "graph.ttl":
            return ex.to_turtle(ex.knowledge_graph(kn)), "text/turtle"
        if name == "taxonomy.jsonld":
            return json.dumps(kn.taxonomy.to_jsonld(), indent=2), "application/ld+json"
        if name == "glossary.jsonld":
            return json.dumps(kn.glossary.to_jsonld() if kn.glossary else {}, indent=2), "application/ld+json"
        if name == "ontology.jsonld":
            return json.dumps(kn.ontology.to_jsonld(), indent=2), "application/ld+json"
        if name == "shapes.ttl":
            return ex.shacl_shapes(kn), "text/turtle"
        if name == "knowledge.graphml":
            return ex.to_graphml(kn), "application/xml"
        raise NotFound(f"no export {name!r}")

    def page(self) -> str:
        from .viewer import render_live_html
        return render_live_html(self.title or "Knowledge")

    # ---------------------------------------------------------------- routing
    def handle(self, path: str, query: dict[str, list[str]], if_none_match: str | None = None
               ) -> tuple[int, str, bytes, dict[str, str]]:
        """(status, content type, body, extra headers) for one GET. Kept free of HTTP plumbing so any web
        framework can call it."""
        def js(obj: Any, status: int = 200, headers: dict[str, str] | None = None):
            return status, "application/json", json.dumps(obj, default=str).encode(), headers or {}
        arg = lambda k, d=None: (query.get(k) or [d])[0]
        parts = [unquote(p) for p in path.strip("/").split("/") if p]
        try:
            if not parts or parts == ["index.html"]:
                return 200, "text/html; charset=utf-8", self.page().encode(), {}
            if parts[0] != "api":
                raise NotFound(path)
            rest = parts[1:]
            if rest == ["health"]:
                return js({"ok": True, "source": "files" if self.files else "mongodb",
                           "defaultRelease": self.default_release})
            if rest == ["releases"]:
                return js({"default": self.default_release, "releases": self.releases()})
            if len(rest) >= 3 and rest[0] == "releases":
                release, what, tail = rest[1], rest[2], rest[3:]
                if what == "view" and not tail:
                    _, view, etag = self.load(release)
                    if if_none_match == etag:
                        return 304, "application/json", b"", {"ETag": etag}
                    return js(view, headers={"ETag": etag, "Cache-Control": "no-cache"})
                if what == "rules" and len(tail) == 1:
                    return js(self.rule(release, tail[0]))
                if what == "graph" and not tail:
                    return js(self.graph(release, arg("agent"), arg("focus"), int(arg("depth", "1"))))
                if what == "search" and not tail:
                    return js(self.search(release, arg("q", ""), int(arg("limit", "25"))))
                if what == "export" and len(tail) == 1:
                    body, ctype = self.export(release, tail[0])
                    return 200, ctype, body.encode(), {"Content-Disposition": f'inline; filename="{tail[0]}"'}
                if not tail:
                    return js(self.section(release, what))
            raise NotFound(path)
        except NotFound as e:
            return js({"error": str(e)}, HTTPStatus.NOT_FOUND)
        except LookupError as e:
            return js({"error": str(e)}, HTTPStatus.NOT_FOUND)


def make_server(service: KnowledgeService, host: str = "127.0.0.1", port: int = 8300) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            status, ctype, body, headers = service.handle(u.path, parse_qs(u.query), self.headers.get("If-None-Match"))
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        do_HEAD = do_GET

        def log_message(self, fmt, *args):  # quieter: one line per request
            print(f"{self.address_string()} {fmt % args}")

    return ThreadingHTTPServer((host, port), Handler)
