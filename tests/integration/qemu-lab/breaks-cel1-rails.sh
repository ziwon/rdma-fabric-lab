#!/usr/bin/env bash
# shellcheck disable=SC2016 # single-quoted commands expand inside the guests
# The deliberate breaks of cel1-rail-networks on a running cel1 simulation that the fleet targets built. Each break runs
# the scenario, which must fail with the named checks failed, then repairs the break and runs the scenario again, which
# must pass. Steps land in .lab/breaks-cel1-rails/<UTC time>/ with steps.tsv, and each break with its two run ids in
# pairs.tsv. LAB_DIR moves the guests' files, as for lab.sh.
#
#   breaks-cel1-rails.sh [break name...]     only the named breaks, every one without a name
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
out=$repo/.lab/breaks-cel1-rails/$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$out/configs"
cd "$repo"
export LAB_PROFILE=cel1 PATH=$repo/.venv/bin:$PATH
export LAB_DIR=${LAB_DIR:-$repo/.lab/cel1}
connect=$LAB_DIR/inventory.yml
fleet=(CONFIRM=cel1 "FLEET_CONNECT=$connect")
n=0
printf 'step\texpect\texit\tlog\n' > "$out/steps.tsv"
printf 'break\tbroken_run\tbroken_status\tfailed_checks\trepaired_run\trepaired_status\n' > "$out/pairs.tsv"

lab() { "$here/lab.sh" "$@"; }
kc() { lab ssh cel1-cp-02 "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf $*" < /dev/null; }
rails() { make --no-print-directory fleet-rails-test "FLEET_CONNECT=$connect" "$@"; }
value() { python3 -c 'import sys, yaml; print(yaml.safe_load(open(sys.argv[1]))[sys.argv[2]])' "$1" "$2"; }

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
manifest_of() { grep -oE 'results/[0-9]{8}T[0-9]{6}Z/cel1/cel1-rail-networks' "$(last_log "$1")" | head -n1; }

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

broken_run() {
  local name=$1 what=$2
  shift 2
  RFL_BREAK=$name RFL_BREAK_WHAT=$what "$@"
}

# breaks <name> <checks that must fail> -- <break command> -- <repair command> [-- <broken run options>]
selected=("$@")
breaks() {
  local name=$1 checks=$2
  shift 2
  if [[ ${#selected[@]} -gt 0 && " ${selected[*]} " != *" $name "* ]]; then return 0; fi
  local -a brk=() fix=() opts=()
  [[ $1 == -- ]] && shift
  while [[ $# -gt 0 && $1 != -- ]]; do brk+=("$1"); shift; done
  [[ $# -gt 0 ]] && shift
  while [[ $# -gt 0 && $1 != -- ]]; do fix+=("$1"); shift; done
  [[ $# -gt 0 ]] && shift
  opts=("$@")
  if [[ ${#brk[@]} -gt 0 ]]; then step "break-$name" 0 "${brk[@]}"; fi
  step "run-$name-broken" nonzero broken_run "$name" "breaks-cel1-rails.sh break $name: ${brk[*]:-the options ${opts[*]}}" rails "${opts[@]}"
  # shellcheck disable=SC2086 # checks is a space-separated list of check ids
  step "failed-$name" 0 failed_with "run-$name-broken" $checks
  if [[ ${#fix[@]} -gt 0 ]]; then step "repair-$name" 0 "${fix[@]}"; fi
  step "run-$name-repaired" 0 rails
  record_pair "$name" "run-$name-broken" "run-$name-repaired"
}

config=tracks/cel1/config/cel1-rail-networks.yaml
config_with() { # <out name> <python statement on c>
  python3 - "$config" "$out/configs/$1" "$2" <<'EOF'
import sys, yaml
c = yaml.safe_load(open(sys.argv[1]))
exec(sys.argv[3])
open(sys.argv[2], "w").write(yaml.safe_dump(c, sort_keys=False))
EOF
  printf 'CONFIG=%s\n' "$out/configs/$1"
}

boot_node=$(value "$config" boot_node)
rdma_node=$(value "$config" rdma_pod_node)
attach_node=$(value "$config" attach_pod_node)
operator_ns=$(value "$config" operator_namespace)
cilium_conf=$(python3 -c 'import yaml; print(yaml.safe_load(open("helm/cilium/values-cel1.yaml"))["cni"]["confPath"].rstrip("/") + "/05-cilium.conflist")')
rail_ns=$(value helm/rail-networks/values-cel1.yaml namespace)
unlabeled=cel1-dgx-08

# The ipoib-cni pod of a node, deleted so its DaemonSet starts a new one, which copies the plugin binary again.
ipoib_restart() {
  kc "-n $operator_ns delete pod --field-selector spec.nodeName=$1 -l name=ipoib-cni --wait --timeout=120s"
  kc "-n $operator_ns wait --for=condition=Ready pod --field-selector spec.nodeName=$1 -l name=ipoib-cni --timeout=180s"
}
test_image=$(python3 -c 'import yaml; c = yaml.safe_load(open("versions.lock.yaml"))["components"]["lab.k8s_test_image"]; print(c["ref"].rsplit(":", 1)[0] + "@" + c["value"])')
stray_pod() { kc "-n $rail_ns run rfl-break-stray --image=$test_image --labels=rdma-fabric-lab/rail-test=stray --restart=Never --command -- sleep 3600"; }
ipoib_break() { lab ssh "$attach_node" 'sudo rm /opt/cni/bin/ipoib && ls /opt/cni/bin' < /dev/null; }
decoy() { lab ssh "$rdma_node" "sudo systemd-run --unit rfl-break-decoy -p Restart=always -p RestartSec=200ms --setenv=RFL_RUN=$1 sleep 600" < /dev/null; }
undecoy() { lab ssh "$rdma_node" 'sudo systemctl stop rfl-break-decoy; sudo systemctl reset-failed rfl-break-decoy 2>/dev/null; true' < /dev/null; }

breaks cilium-file-back-in-cni-dir "cni-layout-on-every-node pod-at-boot-waits-for-multus" \
  -- lab ssh "$boot_node" "sudo cp $cilium_conf /etc/cni/net.d/05-cilium.conflist" \
  -- make fleet-cni-migrate "${fleet[@]}"
breaks rail-host-unlabeled "operator-runs-three-components-on-rail-hosts pools-give-each-rail-host-a-block rdma-resource-registered-without-a-device" \
  -- kc "label node $unlabeled rfl.example/node-class-" \
  -- make fleet-k8s "${fleet[@]}"
breaks rail-master-changed "rail-attachments-name-ipoib-on-each-rail" \
  -- kc "patch ipoibnetworks.mellanox.com cel1-rail3 --type merge -p '{\"spec\":{\"master\":\"stor0\"}}'" \
  -- make fleet-addons "${fleet[@]}" RELEASE=rail-networks FORCE=1
breaks rdma-pod-asks-for-nothing "rdma-pod-waits-without-a-device" \
  -- -- -- "$(config_with units.yaml 'c["rdma_pod_units"] = 0')"
breaks ipoib-plugin-missing "ipoib-refuses-the-ethernet-rails" \
  -- ipoib_break \
  -- ipoib_restart "$attach_node"
breaks stray-rail-test-pod "rail-test-pods-removed" \
  -- stray_pod \
  -- kc "-n $rail_ns delete pod rfl-break-stray --wait --timeout=60s"
decoy_id=$(date -u +%Y%m%dT%H%M%SZ)
breaks respawning-run-process "no-process-left" \
  -- decoy "$decoy_id" \
  -- undecoy -- RUN="$decoy_id"

step validate 0 make validate
printf '\nsteps: %s\npairs: %s\n' "$out/steps.tsv" "$out/pairs.tsv"
awk -F'\t' 'NR > 1 && (($2 == "0" && $3 != 0) || ($2 == "nonzero" && $3 == 0)) { bad++ } END { print (bad ? bad : 0) " unexpected exit codes"; exit (bad ? 1 : 0) }' "$out/steps.tsv"
