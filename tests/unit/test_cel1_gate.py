import copy
import json
from pathlib import Path

from fleet import gate

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "fixtures/fleet/cel1-inventory-list.json").read_text())
INVENTED = {"cel1"}


def simulated(listing=FIXTURE):
    merged = copy.deepcopy(listing)
    for n, (host, own) in enumerate(sorted(merged["_meta"]["hostvars"].items())):
        own.update(ansible_host="127.0.0.1", ansible_port=22301 + n)
    merged["_meta"]["hostvars"]["cel1-cp-01"]["rfl_kubeconfig_server"] = "https://127.0.0.1:22340"
    return merged


def test_the_site_inventory_alone_is_refused_for_every_host():
    found = gate.inventory_refusals(FIXTURE, INVENTED)
    assert len([f for f in found if "ansible_host is unset" in f]) == 13
    assert any(f.startswith("cel1-cp-01: rfl_kubeconfig_server is unset") for f in found)


def test_the_simulation_override_passes():
    assert gate.inventory_refusals(simulated(), INVENTED) == []


def test_one_host_pointed_elsewhere_is_named():
    merged = simulated()
    merged["_meta"]["hostvars"]["cel1-dgx-04"]["ansible_host"] = "10.140.0.24"
    assert gate.inventory_refusals(merged, INVENTED) == ["cel1-dgx-04: ansible_host is 10.140.0.24, not 127.0.0.1"]


def test_a_kubeconfig_server_inside_the_site_is_refused_even_when_the_hosts_are_simulated():
    merged = simulated()
    merged["_meta"]["hostvars"]["cel1-cp-01"]["rfl_kubeconfig_server"] = "https://10.140.0.10:6443"
    assert gate.inventory_refusals(merged, INVENTED)[0].startswith("cel1-cp-01: rfl_kubeconfig_server is https://10.140.0.10:6443")


def test_an_inventory_of_a_site_that_is_not_invented_passes_through():
    other = copy.deepcopy(FIXTURE)
    for own in other["_meta"]["hostvars"].values():
        own["rfl_inventory"] = "elsewhere"
    assert gate.inventory_refusals(other, INVENTED) == []


def test_kubeconfig_servers_must_be_forwards_on_loopback():
    ok = {"clusters": [{"name": "kubernetes", "cluster": {"server": "https://127.0.0.1:22340"}}]}
    assert gate.kubeconfig_refusals(ok) == []
    bad = {"clusters": [{"name": "kubernetes", "cluster": {"server": "https://10.140.0.10:6443"}}]}
    assert gate.kubeconfig_refusals(bad) == ["cluster kubernetes: server https://10.140.0.10:6443 is not https://127.0.0.1:<forward>"]
    assert gate.kubeconfig_refusals({}) == ["the kubeconfig names no cluster"]
    assert gate.kubeconfig_refusals({"clusters": [{"name": "k", "cluster": {"server": "https://127.0.0.1.example:1"}}]})
