from pathlib import Path

import yaml

from run import hostnet

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "a-soft-roce" / "k8s-rdma"
CONFIG = yaml.safe_load(hostnet.CONFIG.read_text())
DIGEST = "sha256:" + "cd" * 32
RUN_ID = "20261116T000000Z"


def pod(profile: str = "privileged-nonroot") -> dict:
    return hostnet.rdma_pod("ns", RUN_ID, "rdma-server", "node-a", f"img@{DIGEST}", CONFIG["profiles"][profile], CONFIG)


def test_rdma_pods_use_the_node_network_and_the_pinned_image_on_one_node():
    spec = pod()["spec"]
    assert spec["hostNetwork"] is True and spec["dnsPolicy"] == "ClusterFirstWithHostNet"
    assert spec["nodeSelector"] == {"kubernetes.io/hostname": "node-a"}
    container = spec["containers"][0]
    assert container["image"] == f"img@{DIGEST}" and container["imagePullPolicy"] == "Never"
    assert {"name": "RFL_RUN", "value": RUN_ID} in container["env"]
    assert "hostPID" not in spec and "hostIPC" not in spec and "volumes" not in spec


def test_only_the_device_files_profile_mounts_dev_infiniband():
    spec = pod("unprivileged-device-files")["spec"]
    assert spec["volumes"] == [{"name": "infiniband", "hostPath": {"path": "/dev/infiniband", "type": "Directory"}}]


def test_the_declared_profile_passes_and_a_host_pid_or_a_missing_privilege_fails():
    profile = CONFIG["profiles"]["privileged-nonroot"]
    assert hostnet.security_problems(pod(), profile) == []
    widened = pod()
    widened["spec"]["hostPID"] = True
    widened["spec"]["containers"][0]["securityContext"].pop("privileged")
    problems = hostnet.security_problems(widened, profile)
    assert any("hostPID" in p for p in problems) and any("privileged" in p for p in problems)


def test_running_pods_must_report_the_locked_digest():
    running = {"metadata": {"name": "p"}, "status": {"containerStatuses": [{"imageID": f"docker.io/library/x@{DIGEST}"}]}}
    assert hostnet.image_digest_problems(running, DIGEST) == []
    assert hostnet.image_digest_problems(running, "sha256:" + "00" * 32)
    assert hostnet.image_digest_problems({"metadata": {"name": "p"}, "status": {}}, DIGEST) == ["pod p reports no container"]


def test_podsecurity_warnings_count_every_pod_they_name():
    stderr = (FIX / "podsecurity-dry-run-baseline.stderr").read_text()
    assert hostnet.podsecurity_refused(stderr) == 2
    assert hostnet.podsecurity_refused('Warning: existing pods in namespace "x" violate the new PodSecurity enforce level\n') == 0


def test_devinfo_of_an_active_rxe_port_passes_and_another_device_fails():
    text = (FIX / "ibv-devinfo.stdout").read_text()
    assert hostnet.devinfo_problems(text, "rxe0") == []
    assert hostnet.devinfo_problems(text.replace("PORT_ACTIVE", "PORT_DOWN"), "rxe0") == ["port not PORT_ACTIVE"]
    assert hostnet.devinfo_problems(text, "rxe1") == ["no hca_id rxe1"]


def test_memlock_limits_are_read_from_proc_limits():
    assert hostnet.memlock_limits((FIX / "limits-pod.stdout").read_text()) == ("8388608", "8388608")
    assert hostnet.memlock_limits((FIX / "limits-ssh-user.stdout").read_text()) == ("776622080", "776622080")
    assert hostnet.memlock_limits("Max locked memory         unlimited            unlimited            bytes\n") == ("unlimited", "unlimited")
    assert hostnet.memlock_limits("") is None


def test_the_pods_repeat_the_vm_baseline_parameters():
    merged = hostnet.load_config()
    vm = yaml.safe_load(hostnet.rxe.CONFIG.read_text())
    assert {k: merged[k] for k in ("server", "client", "rping", "perftest", "capture")} == \
        {k: vm[k] for k in ("server", "client", "rping", "perftest", "capture")}
