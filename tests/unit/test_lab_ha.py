import json
from pathlib import Path

from run import ha

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "a-soft-roce" / "ha"
PROBES = ha.parse_probes((FIX / "probes.txt").read_text())
KILLED_AT = 1794720149.4724123


def test_outage_of_the_recorded_power_cut_is_first_failure_to_first_success():
    result = ha.outage(PROBES, KILLED_AT)
    assert result["failures"] == 6 and result["steady"]
    assert result["unavailable_s"] == round(result["recovered_at"] - result["first_fail"], 3)
    assert 8 < result["unavailable_s"] < 10


def test_probes_before_the_power_cut_do_not_count():
    assert ha.outage(PROBES, 0)["probes"] > ha.outage(PROBES, KILLED_AT)["probes"]


def test_no_recovery_leaves_the_outage_unmeasured():
    failing = [(t, False) for t, _ in PROBES]
    result = ha.outage(failing, KILLED_AT)
    assert result["recovered_at"] is None and result["unavailable_s"] is None and not result["steady"]


def test_no_failure_is_a_zero_outage():
    assert ha.outage([(t, True) for t, _ in PROBES], KILLED_AT)["unavailable_s"] == 0.0


def test_the_vip_is_found_on_the_holder_only():
    listing = json.loads((FIX / "ha-cp2-addr.json").read_text())
    assert ha.vip_carriers({"ha-cp2": listing, "ha-cp1": []}, "192.168.140.20") == ["ha-cp2"]
    assert ha.vip_carriers({"ha-cp2": listing}, "192.168.140.99") == []


def test_etcd_members_and_healthy_endpoints_are_read_from_etcdctl_json():
    assert ha.etcd_members(json.loads((FIX / "etcd-members.json").read_text())) == ["ha-cp1", "ha-cp2", "ha-cp3"]
    healthy = ha.healthy_endpoints(json.loads((FIX / "etcd-health-one-down.json").read_text()))
    assert healthy == ["https://192.168.140.21:2379", "https://192.168.140.23:2379"]


def test_probe_loop_asks_livez_on_the_vip_with_a_bounded_timeout():
    command = ha.probe_command("192.168.140.20", 6443, 0.5, 1)
    assert "https://192.168.140.20:6443/livez" in command and "-m 1" in command and "sleep 0.5" in command


def test_lease_holder_is_none_without_a_holder():
    assert ha.lease_holder({"spec": {"holderIdentity": "ha-cp1"}}) == "ha-cp1"
    assert ha.lease_holder({}) is None
