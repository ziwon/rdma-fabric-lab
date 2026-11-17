"""The learning site under .site/ against the repository it describes. These read source files only, so they
run without Node. The build itself fails on a missing check through .site/scripts/verify-dist.mjs."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / ".site"
PAGES = SITE / "src" / "content" / "docs"
WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"

CHECKS_TAG = re.compile(r'<ScenarioChecks\s+spec="([^"]+)"\s*/>')
EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
PINNED_ACTION = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def declared_specs() -> set[str]:
    specs = set()
    for path in ROOT.glob("tracks/*/scenarios/*.yaml"):
        spec = yaml.safe_load(path.read_text())
        specs.add(f"{spec['track']}/{spec['scenario']}")
    return specs


def shown_specs() -> list[str]:
    return [m for page in PAGES.rglob("*.mdx") for m in CHECKS_TAG.findall(page.read_text())]


def test_every_scenario_spec_has_exactly_one_page_that_shows_its_checks():
    shown = shown_specs()
    declared = declared_specs()
    assert declared, "no scenario spec found under tracks/*/scenarios/"
    assert sorted(set(shown) - declared) == [], "a page shows a scenario that has no spec"
    assert sorted(declared - set(shown)) == [], "a scenario spec has no page that shows its checks"
    assert sorted(s for s in set(shown) if shown.count(s) > 1) == [], "a scenario's checks appear on two pages"


def test_site_dependencies_are_exact_versions():
    package = json.loads((SITE / "package.json").read_text())
    loose = {name: v for name, v in package["dependencies"].items() if not EXACT_VERSION.match(v)}
    assert loose == {}


def test_lock_file_resolves_each_direct_dependency_to_its_pinned_version():
    package = json.loads((SITE / "package.json").read_text())
    lock = json.loads((SITE / "package-lock.json").read_text())["packages"]
    resolved = {name: lock[f"node_modules/{name}"]["version"] for name in package["dependencies"]}
    assert resolved == package["dependencies"]


def test_workflow_pins_every_action_by_commit_and_uses_no_secret():
    text = WORKFLOW.read_text()
    uses = [line.split("uses:", 1)[1].split("#")[0].strip() for line in text.splitlines() if "uses:" in line]
    assert uses, "the workflow uses no action"
    assert [u for u in uses if not PINNED_ACTION.match(u)] == []
    assert "secrets." not in text


def test_workflow_builds_pull_requests_and_deploys_only_pushes_to_main_once_the_owner_opts_in():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"pull_request", "push"}
    assert triggers["push"]["branches"] == ["main"]
    main_only = "github.event_name == 'push' && github.ref == 'refs/heads/main' && vars.PAGES_ENABLED == 'true'"
    assert workflow["jobs"]["deploy"]["if"] == main_only
    upload = [s for s in workflow["jobs"]["build"]["steps"] if "upload-pages-artifact" in s.get("uses", "")]
    assert len(upload) == 1 and upload[0]["if"] == main_only
    assert workflow["permissions"] == {"contents": "read"}
