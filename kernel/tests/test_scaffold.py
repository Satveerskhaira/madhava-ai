"""The bundled templates scaffold a domain agents project and a core project that start green."""

import asyncio
import importlib
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

from rulegraph import Settings
from rulegraph.evaluation import check
from rulegraph.models import ContextRef, DomainTask
from rulegraph.rules.validate import validate
from rulegraph.scaffold import scaffold

TODAY = date(2026, 10, 14)


@pytest.fixture(scope="module")
def projects(tmp_path_factory):
    root = tmp_path_factory.mktemp("scaffold")
    scaffold("subagents", root / "agents", "shop_agents")
    scaffold("core", root / "core", "shop_core", agents_package="shop_agents")
    for p in (root / "agents" / "src", root / "core" / "src"):
        sys.path.insert(0, str(p))
    yield root
    for name in [m for m in sys.modules if m.startswith(("shop_agents", "shop_core"))]:
        del sys.modules[name]


def goldens(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text())["goldens"]


def test_files_are_rendered(projects):
    files = [p for p in projects.rglob("*") if p.is_file()]
    assert not any(p.name.endswith(".tmpl") or "__PACKAGE__" in str(p) for p in files)
    assert (projects / "agents" / ".github" / "workflows" / "ci.yml").exists()
    assert not any("__" + "PACKAGE__" in p.read_text() or "__DIST__" in p.read_text() for p in files)


def test_subagents_project_is_green(projects):
    pkg = importlib.import_module("shop_agents")
    system = pkg.build(Settings(today=TODAY))
    assert validate(system.knowledge) == []
    assert set(system.agents) == {"credit", "fulfillment", "charges"}
    for g in goldens(projects / "agents" / "goldens" / "domain_goldens.yaml"):
        exp = asyncio.run(system.agents[g["agent"]].ask(g["question"], g["record"]))
        assert check(exp, g["expect"]) == [], exp.answer


def test_domain_agent_reports_out_of_scope_token_as_gap(projects):
    system = importlib.import_module("shop_agents").build(Settings(today=TODAY))
    snap = system.toolbox.get_record_outcome("ORD-1")
    ctx = ContextRef(record_id="ORD-1", reference_id="ACC-1", service_date=date(2026, 9, 10),
                     rules_date=system.toolbox.rules_date(snap, "as_decided"))
    report = asyncio.run(system.agents["fulfillment"].handle(
        DomainTask(task_id="t", agent="fulfillment", context=ctx, outcome_tokens=["reject:CR1"])))
    assert not [f for f in report.findings if f.explains] and report.gaps == ["reject:CR1"]


def test_core_project_is_green(projects):
    system = importlib.import_module("shop_core").build(Settings(today=TODAY))
    for g in goldens(projects / "core" / "goldens" / "goldens.yaml"):
        exp = asyncio.run(system.core.ask(g["question"], g["record"]))
        assert check(exp, g["expect"]) == [], exp.answer


def test_scaffold_refuses_bad_names_and_full_dirs(tmp_path):
    with pytest.raises(ValueError):
        scaffold("subagents", tmp_path / "x", "Bad-Name")
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "setup.py").write_text("")
    with pytest.raises(FileExistsError):
        scaffold("subagents", tmp_path / "full", "ok_name")
