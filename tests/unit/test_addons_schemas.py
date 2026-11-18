"""CRD schemas for kubeconform, so the custom resources of a release are checked instead of skipped."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from addons import schemas

ROOT = Path(__file__).resolve().parents[2]
KUBECONFORM = ROOT / ".tools" / "bin" / "kubeconform"
needs_kubeconform = pytest.mark.skipif(not KUBECONFORM.is_file(), reason="make tools installs the pinned kubeconform")

CRD = {
    "apiVersion": "apiextensions.k8s.io/v1",
    "kind": "CustomResourceDefinition",
    "metadata": {"name": "pools.example.test"},
    "spec": {
        "group": "example.test",
        "names": {"kind": "Pool", "plural": "pools"},
        "scope": "Namespaced",
        "versions": [
            {
                "name": "v1",
                "served": True,
                "storage": True,
                "schema": {"openAPIV3Schema": {
                    "type": "object",
                    "properties": {
                        "spec": {
                            "type": "object",
                            "required": ["subnet"],
                            "properties": {
                                "subnet": {"type": "string"},
                                "gateway": {"type": "string", "nullable": True},
                                "size": {"x-kubernetes-int-or-string": True},
                                "routes": {"type": "array", "items": {"type": "object", "properties": {"dst": {"type": "string"}}}},
                                "extra": {"type": "object", "x-kubernetes-preserve-unknown-fields": True,
                                          "properties": {"known": {"type": "string"}}},
                            },
                        },
                    },
                }},
            },
            {"name": "v0", "served": False, "storage": False, "schema": {"openAPIV3Schema": {"type": "object"}}},
        ],
    },
}


def test_an_object_that_lists_its_properties_allows_no_other_key():
    spec = schemas.strict(CRD["spec"]["versions"][0]["schema"]["openAPIV3Schema"])["properties"]["spec"]
    assert spec["additionalProperties"] is False
    assert spec["properties"]["routes"]["items"]["additionalProperties"] is False


def test_preserved_unknown_fields_stay_open_and_nullable_and_int_or_string_become_types():
    spec = schemas.strict(CRD["spec"]["versions"][0]["schema"]["openAPIV3Schema"])["properties"]["spec"]["properties"]
    assert "additionalProperties" not in spec["extra"]
    assert spec["gateway"]["type"] == ["string", "null"] and "nullable" not in spec["gateway"]
    assert spec["size"]["type"] == ["integer", "string"]


def test_the_conversion_leaves_the_crd_unchanged():
    before = json.dumps(CRD, sort_keys=True)
    schemas.strict(CRD["spec"]["versions"][0]["schema"]["openAPIV3Schema"])
    assert json.dumps(CRD, sort_keys=True) == before


def test_each_served_version_gets_a_file_where_the_location_finds_it(tmp_path):
    stale = tmp_path / "schemas" / "old.test" / "gone_v1.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}")
    written = schemas.write([CRD], tmp_path / "schemas")
    assert written == [tmp_path / "schemas" / "example.test" / "pool_v1.json"]
    assert not stale.exists()
    root = json.loads(written[0].read_text())
    assert {"apiVersion", "kind", "metadata", "spec"} <= set(root["properties"])
    assert root["additionalProperties"] is False


def test_crds_are_read_from_a_manifest_and_named_by_group_and_kind():
    manifest = yaml.safe_dump_all([{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "a"}}, CRD, None])
    found = schemas.crds(manifest)
    assert [d["metadata"]["name"] for d in found] == ["pools.example.test"]
    assert schemas.kinds(found) == {("example.test", "Pool")}


def test_a_skipped_custom_resource_is_a_problem_and_a_skipped_crd_is_not():
    results = [
        {"kind": "Pool", "name": "a", "version": "example.test/v1", "status": "statusSkipped"},
        {"kind": "CustomResourceDefinition", "name": "pools.example.test", "version": "apiextensions.k8s.io/v1",
         "status": "statusSkipped"},
        {"kind": "ConfigMap", "name": "b", "version": "v1", "status": "statusValid"},
        {"kind": "Pool", "name": "c", "version": "example.test/v1", "status": "statusInvalid", "msg": "bad"},
    ]
    counts, problems = schemas.summary(results, {("example.test", "Pool")})
    assert counts == {"valid": 1, "invalid": 1, "error": 0, "skipped": 2}
    assert problems == ["Pool a: skipped, though a CRD of this environment defines Pool", "Pool c: bad"]


@needs_kubeconform
def test_kubeconform_refuses_a_misspelled_key_of_a_custom_resource(tmp_path):
    schemas.write([CRD], tmp_path / "schemas")
    good = {"apiVersion": "example.test/v1", "kind": "Pool", "metadata": {"name": "a"},
            "spec": {"subnet": "192.0.2.0/24", "gateway": None, "size": "10%", "extra": {"anything": 1}}}
    bad = {**good, "metadata": {"name": "b"}, "spec": {"subnet": "192.0.2.0/24", "gatway": "192.0.2.1"}}
    manifest = tmp_path / "pools.yaml"
    manifest.write_text(yaml.safe_dump_all([good, bad]))
    out = subprocess.run([str(KUBECONFORM), "-strict", "-output", "json", "-verbose",
                          "-schema-location", str(tmp_path / "schemas" / schemas.LOCATION), str(manifest)],
                         capture_output=True, text=True)
    status = {r["name"]: r["status"] for r in json.loads(out.stdout)["resources"]}
    assert status == {"a": "statusValid", "b": "statusInvalid"}
