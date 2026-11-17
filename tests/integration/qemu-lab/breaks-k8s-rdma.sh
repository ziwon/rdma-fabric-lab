#!/usr/bin/env bash
# shellcheck disable=SC2016 # single-quoted commands expand inside the guests
# The deliberate breaks of k8s-hostnet-rdma and k8s-multus-net1 on running lab guests that have Kubernetes, the addons and
# the RDMA test image. Each break runs its scenario, which must fail with the named checks failed, then
# repairs the break and runs the scenario again, which must pass. Steps land in
# .lab/breaks-k8s-rdma/<UTC time>/ with steps.tsv, and each break with its two run ids in pairs.tsv.
# LAB_DIR moves the guests' files, as for lab.sh.
#
#   breaks-k8s-rdma.sh [break name...]     only the named breaks, every one without a name
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
out=$repo/.lab/breaks-k8s-rdma/$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$out/configs"
cd "$repo"
export LAB_PROFILE=lab PATH=$repo/.venv/bin:$PATH
read -r -a pair <<< "$("$here/lab.sh" inventories)"
inventories=(-i "${pair[0]}" -i "${pair[1]}")
n=0
printf 'step\texpect\texit\tlog\n' > "$out/steps.tsv"
printf 'break\tbroken_run\tbroken_status\tfailed_checks\trepaired_run\trepaired_status\n' > "$out/pairs.tsv"

lab() { "$here/lab.sh" "$@"; }
kc() { lab ssh cp1 "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf $*" < /dev/null; }
hv() { ansible-inventory "${inventories[@]}" --host "$1" | python3 -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$2"; }
hostnet() { PYTHONPATH=scripts python -m run.hostnet "${inventories[@]}" "$@"; }
multus() { PYTHONPATH=scripts python -m run.multus test "${inventories[@]}" "$@"; }

step() {
  local name=$1 expect=$2 rc=0 log
  shift 2
  n=$((n + 1))
  log=$(printf '%s/%02d-%s.log' "$out" "$n" "$name")
  printf '$ %s\n' "$*" > "$log"
  "$@" >> "$log" 2>&1 || rc=$?
  printf '%s\t%s\t%s\t%s\n' "$name" "$expect" "$rc" "${log#"$repo"/}" >> "$out/steps.tsv"
  local verdict=ok
  if [[ $expect == 0 && $rc != 0 ]] || [[ $expect == nonzero && $rc == 0 ]]; then verdict=UNEXPECTED; fi
  printf '%02d %-44s expect %-7s exit %-3s %s\n' "$n" "$name" "$expect" "$rc" "$verdict"
}

last_log() { local logs=("$out"/*-"$1".log); printf '%s\n' "${logs[-1]}"; }
manifest_of() { grep -oE 'results/[0-9]{8}T[0-9]{6}Z/a-soft-roce/[a-z0-9-]+' "$(last_log "$1")" | head -n1; }

# The run of step $1 ended failed, and each of the remaining arguments is one of its failed checks.
failed_with() {
  local dir
  dir=$(manifest_of "$1")
  shift
  python3 - "$repo/$dir/manifest.json" "$@" <<'EOF'
import json, sys
manifest = json.load(open(sys.argv[1]))
failed = [c["id"] for c in manifest["checks"] if c["status"] == "failed"]
missing = [c for c in sys.argv[2:] if c not in failed]
print(f"{manifest['run_id']} {manifest['status']}; failed {failed}; want failed {sys.argv[2:]}; missing {missing}")
sys.exit(manifest["status"] != "failed" or bool(missing))
EOF
}

record_pair() {
  local name=$1 broken=$2 repaired=$3 bdir rdir
  bdir=$(manifest_of "$broken")
  rdir=$(manifest_of "$repaired")
  python3 - "$name" "$repo/$bdir/manifest.json" "$repo/$rdir/manifest.json" >> "$out/pairs.tsv" <<'EOF'
import json, sys
b, r = (json.load(open(p)) for p in sys.argv[2:])
failed = ",".join(c["id"] for c in b["checks"] if c["status"] == "failed")
print("\t".join([sys.argv[1], b["run_id"], b["status"], failed, r["run_id"], r["status"]]))
EOF
}

# broken_run <name> <what> <command...>: the run's manifest names the deliberate break it carries.
broken_run() {
  local name=$1 what=$2
  shift 2
  RFL_BREAK=$name RFL_BREAK_WHAT=$what "$@"
}

# break <name> <scenario: hostnet|multus> <checks that must fail> -- <break command> -- <repair command> [-- <broken run options>]
selected=("$@")
breaks() {
  local name=$1 scenario=$2 checks=$3
  shift 3
  if [[ ${#selected[@]} -gt 0 && " ${selected[*]} " != *" $name "* ]]; then return 0; fi
  local -a brk=() fix=() opts=()
  [[ $1 == -- ]] && shift
  while [[ $# -gt 0 && $1 != -- ]]; do brk+=("$1"); shift; done
  [[ $# -gt 0 ]] && shift
  while [[ $# -gt 0 && $1 != -- ]]; do fix+=("$1"); shift; done
  [[ $# -gt 0 ]] && shift
  opts=("$@")
  if [[ ${#brk[@]} -gt 0 ]]; then step "break-$name" 0 "${brk[@]}"; fi
  step "run-$name-broken" nonzero broken_run "$name" "breaks-k8s-rdma.sh break $name: ${brk[*]:-the options ${opts[*]}}" "$scenario" "${opts[@]}"
  # shellcheck disable=SC2086 # checks is a space-separated list of check ids
  step "failed-$name" 0 failed_with "run-$name-broken" $checks
  if [[ ${#fix[@]} -gt 0 ]]; then step "repair-$name" 0 "${fix[@]}"; fi
  step "run-$name-repaired" 0 "$scenario"
  record_pair "$name" "run-$name-broken" "run-$name-repaired"
}

config_with() { # <tracked config> <out name> <python statement on c>
  python3 - "$1" "$out/configs/$2" "$3" <<'EOF'
import sys, yaml
c = yaml.safe_load(open(sys.argv[1]))
exec(sys.argv[3])
open(sys.argv[2], "w").write(yaml.safe_dump(c, sort_keys=False))
EOF
  printf '%s\n' "$out/configs/$2"
}

hostnet_config=tracks/a-soft-roce/config/k8s-hostnet-rdma.yaml
multus_config=tracks/a-soft-roce/config/k8s-multus-net1.yaml
digest_ref() { python3 -c 'import yaml; c = yaml.safe_load(open("versions.lock.yaml"))["components"]["lab.rdma_tools_image"]
print(c["ref"].rsplit(":", 1)[0] + "@" + c["value"], c["ref"])'; }
w1_mgmt=$(hv worker1 lab_mgmt_nic) w2_mgmt=$(hv worker2 lab_mgmt_nic)
w1_lab=$(hv worker1 lab_rdma_nic) w2_lab=$(hv worker2 lab_rdma_nic)
w1_ip=$(hv worker1 lab_rdma_ip) w2_ip=$(hv worker2 lab_rdma_ip) w1_node=$(hv worker1 k8s_node_ip)
prefix=$(python3 -c 'import ipaddress, sys; print(ipaddress.ip_network(sys.argv[1]).prefixlen)' "$(hv worker1 lab_address_plan | python3 -c 'import ast, sys; print(ast.literal_eval(sys.stdin.read())["rdma"])')")

lock_backup=$out/versions.lock.yaml.orig
cp versions.lock.yaml "$lock_backup"
restore_lock() { cp "$lock_backup" versions.lock.yaml; }
trap restore_lock EXIT
change_lock() { python3 - "$1" "$2" <<'EOF'
import sys
from pathlib import Path
p = Path("versions.lock.yaml")
lines, key, value, inside = p.read_text().splitlines(keepends=True), sys.argv[1], sys.argv[2], False
for i, line in enumerate(lines):
    inside = line.startswith(f"  {key}:") or (inside and line.startswith("    "))
    if inside and line.startswith("    value: "):
        lines[i] = f"    value: {value}\n"
        break
p.write_text("".join(lines))
EOF
}
decoy() { lab ssh worker1 "sudo systemd-run --unit rfl-break-decoy -p Restart=always -p RestartSec=200ms --setenv=RFL_RUN=$1 sleep 600" < /dev/null; }
undecoy() { lab ssh worker1 'sudo systemctl stop rfl-break-decoy; sudo systemctl reset-failed rfl-break-decoy 2>/dev/null; true' < /dev/null; }
wait_ns_gone() { kc "wait --for=delete namespace/$(hv worker1 lab_k8s_namespace) --timeout=180s" || true; kc "get namespace $(hv worker1 lab_k8s_namespace) --ignore-not-found"; }
memlock_dropin() {
  if [[ $1 == on ]]; then
    lab ssh worker2 'sudo mkdir -p /etc/systemd/system/containerd.service.d && printf "[Service]\nLimitMEMLOCK=65536\n" | sudo tee /etc/systemd/system/containerd.service.d/rfl-break-memlock.conf && sudo systemctl daemon-reload && sudo systemctl restart containerd && systemctl show containerd -p LimitMEMLOCK' < /dev/null
  else
    lab ssh worker2 'sudo rm -f /etc/systemd/system/containerd.service.d/rfl-break-memlock.conf && sudo systemctl daemon-reload && sudo systemctl restart containerd && systemctl show containerd -p LimitMEMLOCK' < /dev/null
  fi
}
icmp_out() { for w in worker1:"$w1_mgmt" worker2:"$w2_mgmt"; do lab ssh "${w%%:*}" "sudo iptables $1 OUTPUT -o ${w#*:} -p icmp --icmp-type echo-request -m comment --comment rfl-break -j DROP" < /dev/null; done; }
pool_name() { PYTHONPATH=scripts python -m run.multus render "${inventories[@]}" | python3 -c 'import json, sys, yaml
print(json.loads(yaml.safe_load(sys.stdin)["spec"]["config"])["ipam"]["range"].replace("/", "-"))'; }
ghost() { # <add|remove>: one allocation for a pod that does not exist, at the last pool address
  local offset pool
  offset=$(PYTHONPATH=scripts python -m run.multus render "${inventories[@]}" | python3 -c 'import ipaddress, json, sys, yaml
i = json.loads(yaml.safe_load(sys.stdin)["spec"]["config"])["ipam"]
print(int(ipaddress.ip_address(i["range_end"])) - int(ipaddress.ip_network(i["range"]).network_address))')
  pool=$(pool_name)
  if [[ $1 == add ]]; then
    kc "-n kube-system patch ippools.whereabouts.cni.cncf.io $pool --type merge -p '{\"spec\":{\"allocations\":{\"$offset\":{\"id\":\"rfl-break\",\"podref\":\"$(hv worker1 lab_k8s_namespace)/ghost\",\"ifname\":\"net1\"}}}}'"
  else
    kc "-n kube-system patch ippools.whereabouts.cni.cncf.io $pool --type merge -p '{\"spec\":{\"allocations\":{\"$offset\":null}}}'"
  fi
}
# Cilium's own directory, the cni.confPath of the Cilium values, holds the file every break copies.
cilium_conf=$(python3 -c 'import yaml; print(yaml.safe_load(open("helm/cilium/values-lab.yaml"))["cni"]["confPath"].rstrip("/") + "/05-cilium.conflist")')

read -r image_digest_ref image_tag_ref <<< "$(digest_ref)"

# ---------------------------------------------------------------- k8s-hostnet-rdma
breaks image-missing hostnet "rdma-pods-ready" \
  -- lab ssh worker2 "sudo ctr -n k8s.io images rm $image_digest_ref $image_tag_ref" \
  -- make rdma-tools-load
breaks lock-perftest-differs hostnet "pod-tools-match-lock" \
  -- change_lock lab.perftest 0.0-not-the-installed-one \
  -- restore_lock
breaks podsecurity-privileged-refused hostnet "pod-security-as-declared" \
  -- -- -- --config "$(config_with $hostnet_config psa.yaml 'c["podsecurity_levels_refused"].append("privileged")')"
breaks narrower-is-the-working-profile hostnet "narrower-profiles-fail" \
  -- -- -- --config "$(config_with $hostnet_config narrower.yaml 'c["narrower_profiles"].append({"profile": "privileged-nonroot", "probe": "ibv_devinfo", "expect": "No IB devices found"})')"
breaks rxe-deleted-on-worker2 hostnet "ibv-devinfo-in-pods rxe-bound-to-lab-nic rping-verified rdma-read rdma-write rdma-send roce-on-lab-nic" \
  -- lab ssh worker2 "sudo rdma link delete $(hv worker2 lab_rxe_device)" \
  -- make rxe-up
breaks lab-address-removed-on-worker2 hostnet "route-via-lab-nic" \
  -- lab ssh worker2 "sudo ip addr del $w2_ip/$prefix dev $w2_lab" \
  -- lab ssh worker2 "sudo networkctl reconfigure $w2_lab && sleep 5 && ip -br addr show dev $w2_lab"
breaks memlock-64k-on-worker2 hostnet "memlock-in-pods" \
  -- memlock_dropin on \
  -- memlock_dropin off
breaks marker-pings-dropped hostnet "no-roce-on-mgmt-nic" \
  -- icmp_out -I \
  -- icmp_out -D
breaks delete-timeout-1s hostnet "rdma-pods-removed" \
  -- -- wait_ns_gone -- --config "$(config_with $hostnet_config delete.yaml 'c["delete_timeout_s"] = 1')"
decoy_id=$(date -u +%Y%m%dT%H%M%SZ)
breaks respawning-run-process hostnet "no-process-left" \
  -- decoy "$decoy_id" \
  -- undecoy -- --run-id "$decoy_id"

# ---------------------------------------------------------------- k8s-multus-net1
breaks shadow-cni-file-on-worker1 multus "cni-dir-holds-only-multus pods-have-eth0-and-net1" \
  -- lab ssh worker1 "sudo cp $cilium_conf /etc/cni/net.d/00-0-rfl-break.conflist" \
  -- lab ssh worker1 'sudo rm -f /etc/cni/net.d/00-0-rfl-break.conflist'
breaks attachment-crd-deleted multus "network-attachment-from-inventory" \
  -- kc "delete crd network-attachment-definitions.k8s.cni.cncf.io" \
  -- make addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig RELEASE=multus FORCE=1
breaks worker2-cordoned multus "test-pods-ready" \
  -- kc "cordon worker2" \
  -- kc "uncordon worker2"
breaks net1-routes-off-network multus "default-route-on-eth0" \
  -- -- -- --config "$(config_with $multus_config routes.yaml "c['ipam_extra'] = {'routes': [{'dst': '0.0.0.0/1', 'gw': '$w1_ip'}, {'dst': '128.0.0.0/1', 'gw': '$w1_ip'}]}")"
breaks vxlan-dropped-on-worker1 multus "eth0-cross-node" \
  -- lab ssh worker1 "sudo iptables -I INPUT -i $w1_mgmt -d $w1_node -p udp --dport 8472 -m comment --comment rfl-break -j DROP" \
  -- lab ssh worker1 "sudo iptables -D INPUT -i $w1_mgmt -d $w1_node -p udp --dport 8472 -m comment --comment rfl-break -j DROP"
breaks lab-egress-lost-on-worker1 multus "net1-cross-node net1-on-lab-nic" \
  -- lab ssh worker1 "sudo tc qdisc add dev $w1_lab root netem loss 100%" \
  -- lab ssh worker1 "sudo tc qdisc del dev $w1_lab root"
breaks ghost-allocation multus "no-duplicate-addresses recreate-releases-addresses" \
  -- ghost add \
  -- ghost remove
breaks cilium-file-back-in-cni-dir-on-worker2 multus "cni-dir-holds-only-multus pod-at-boot-waits-for-multus new-pod-after-reboot-gets-net1" \
  -- lab ssh worker2 "sudo cp $cilium_conf /etc/cni/net.d/05-cilium.conflist" \
  -- lab ssh worker2 'sudo rm -f /etc/cni/net.d/05-cilium.conflist'
breaks delete-timeout-1s-multus multus "test-namespace-removed" \
  -- -- wait_ns_gone -- --config "$(config_with $multus_config delete.yaml 'c["delete_timeout_s"] = 1')"
decoy_id=$(date -u +%Y%m%dT%H%M%SZ)
breaks respawning-run-process-multus multus "no-process-left" \
  -- decoy "$decoy_id" \
  -- undecoy -- --run-id "$decoy_id"

# The attachment names one master, so differing lab NIC names stop the run before it starts.
printf 'all:\n  hosts:\n    worker2:\n      lab_rdma_nic: other0\n' > "$out/configs/nic-differs.yml"
step refuse-differing-lab-nics nonzero env PYTHONPATH=scripts python -m run.multus render "${inventories[@]}" -i "$out/configs/nic-differs.yml"

step validate 0 make validate
printf '\nsteps: %s\npairs: %s\n' "$out/steps.tsv" "$out/pairs.tsv"
awk -F'\t' 'NR > 1 && (($2 == "0" && $3 != 0) || ($2 == "nonzero" && $3 == 0)) { bad++ } END { print (bad ? bad : 0) " unexpected exit codes"; exit (bad ? 1 : 0) }' "$out/steps.tsv"
