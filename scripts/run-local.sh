#!/usr/bin/env bash
# Run the whole knowledge application on your machine.
#
#   mkdir -p ~/Developer/AI/madhava-ai && cd ~/Developer/AI/madhava-ai
#   git clone -b knowledge-standards https://github.com/Satveerskhaira/madhava-ai.git   # plain clone once PR #6 is merged
#   ./madhava-ai/scripts/run-local.sh            # setup + checks + MongoDB + publish + knowledge service
#
# Layout it creates (the workspace is the folder that holds this repo):
#   <workspace>/madhava-ai            core agent, kernel (rulegraph), pharmacy base pack
#   <workspace>/madhava-ai-subagent   the seven domain agents, one knowledge fragment each
#   <workspace>/.venv                 one Python environment with all four packages installed editable
#   <workspace>/out                   knowledge.html (offline), exports (Turtle, JSON-LD, GraphML, SHACL)
#
# Steps (each can be skipped): clone/update -> install -> test -> validate + goldens + SHACL -> exports
#   -> MongoDB (Docker) -> publish releases -> knowledge service on http://127.0.0.1:8300/
#
# Options (environment variables):
#   BRANCH=main            branch to check out in both repos (default: knowledge-standards if it exists, else main)
#   SKIP_TESTS=1           skip the test suites
#   NO_MONGO=1             do not start MongoDB; serve the knowledge straight from the pack files
#   MONGO_URI=...          use an existing MongoDB instead of the Docker one
#   PORT=8300              knowledge service port
#   NO_SERVE=1             stop after publishing
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"     # .../madhava-ai
WS="$(dirname "$HERE")"                                       # workspace folder
SUB="$WS/madhava-ai-subagent"
OUT="$WS/out"
VENV="$WS/.venv"
PORT="${PORT:-8300}"
MONGO_URI="${MONGO_URI:-mongodb://127.0.0.1:27017}"
GH="https://github.com/Satveerskhaira"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m!! %s\033[0m\n' "$*"; }

pick_branch() {   # $1 = repo dir
  if [ -n "${BRANCH:-}" ]; then echo "$BRANCH"; return; fi
  if git -C "$1" ls-remote --exit-code --heads origin knowledge-standards >/dev/null 2>&1; then
    echo knowledge-standards
  else
    echo main
  fi
}

checkout() {      # $1 = dir, $2 = repo name
  if [ ! -d "$1/.git" ]; then
    say "Cloning $2 into $1"
    git clone "$GH/$2.git" "$1"
  fi
  local b; b="$(pick_branch "$1")"
  say "$2: branch $b"
  git -C "$1" fetch origin "$b"
  git -C "$1" checkout "$b"
  git -C "$1" pull --ff-only origin "$b" || warn "$2 has local changes; left as is"
}

# ---------------------------------------------------------------- 1. code
checkout "$HERE" madhava-ai
checkout "$SUB" madhava-ai-subagent

# ---------------------------------------------------------------- 2. Python environment
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || { echo "Python 3.11 or newer is required (brew install python@3.12)"; exit 1; }
if [ ! -x "$VENV/bin/python" ]; then
  say "Creating $VENV with $PY"
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
. "$VENV/bin/activate"
say "Installing kernel, pharmacy base, domain agents and core agent (editable)"
python -m pip install -q --upgrade pip
python -m pip install -q -e "$HERE/kernel[adk,mcp,rdf,mongo,dev]" -e "$HERE/base[dev]" -e "$SUB[dev]" -e "$HERE[dev]"

# ---------------------------------------------------------------- 3. checks
if [ -z "${SKIP_TESTS:-}" ]; then
  say "Tests: kernel, base, core, domain agents"
  (cd "$HERE" && python -m pytest -q kernel/tests base/tests tests)
  (cd "$SUB" && python -m pytest -q)
fi
say "Knowledge checks"
madhava validate
madhava eval
madhava shacl | tail -n 3

# ---------------------------------------------------------------- 4. files you can open without any server
mkdir -p "$OUT"
say "Offline browser and standard exports in $OUT"
madhava view --no-open --out "$OUT/knowledge.html"
madhava export graph --format ttl --out "$OUT/knowledge.ttl"
madhava export graph --out "$OUT/knowledge.jsonld"
madhava export taxonomy --out "$OUT/taxonomy.jsonld"
madhava export glossary --out "$OUT/glossary.jsonld"
madhava export ontology --out "$OUT/ontology.jsonld"
madhava export shapes --out "$OUT/shapes.ttl"
madhava export graphml --out "$OUT/knowledge.graphml"

# ---------------------------------------------------------------- 5. MongoDB and releases
SERVE_ARGS=()
if [ -z "${NO_MONGO:-}" ]; then
  ping_mongo() { python -c "import pymongo; pymongo.MongoClient('$MONGO_URI', serverSelectionTimeoutMS=1500).admin.command('ping')" >/dev/null 2>&1; }
  if ! ping_mongo && [ "$MONGO_URI" = "mongodb://127.0.0.1:27017" ] && command -v docker >/dev/null 2>&1; then
    say "Starting MongoDB (docker compose, data in a named volume)"
    (cd "$HERE" && docker compose up -d mongo) || warn "docker compose could not start MongoDB"
    for _ in $(seq 1 30); do
      python -c "import pymongo; pymongo.MongoClient('$MONGO_URI', serverSelectionTimeoutMS=1000).admin.command('ping')" \
        >/dev/null 2>&1 && break
      sleep 1
    done
  fi
  if python -c "import pymongo; pymongo.MongoClient('$MONGO_URI', serverSelectionTimeoutMS=2000).admin.command('ping')" >/dev/null 2>&1; then
    say "Publishing release 'current' (base + every installed domain) to $MONGO_URI"
    madhava publish --uri "$MONGO_URI" --release current --note "All domains, from $(git -C "$HERE" rev-parse --short HEAD)"
    say "Publishing release 'pilot-um' (base + utilization management only)"
    rulegraph --pack "$HERE/base/src/pharmacy_base/pack" \
              --fragment "$SUB/src/pharmacy_agents/packs/utilization_management" \
              publish --uri "$MONGO_URI" --release pilot-um --note "Pilot: utilization management only"
    SERVE_ARGS=(--mongo "$MONGO_URI")
  else
    warn "MongoDB is not reachable at $MONGO_URI (no Docker?). Serving from the pack files instead."
  fi
fi

[ -n "${NO_SERVE:-}" ] && { say "Done. Open $OUT/knowledge.html"; exit 0; }

# ---------------------------------------------------------------- 6. knowledge service
say "Knowledge service: http://127.0.0.1:$PORT/   (API: http://127.0.0.1:$PORT/api/releases)  Ctrl+C to stop"
exec madhava knowledge-service ${SERVE_ARGS[@]+"${SERVE_ARGS[@]}"} --port "$PORT" --title "Pharmacy claims knowledge" --open
