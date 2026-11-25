#!/usr/bin/env bash
# shellcheck disable=SC2016 # single-quoted commands expand inside the guests
# Which pods lose net1 when a worker reboots, with the stock CNI paths of Cilium and Multus and with the lab's layout
# (docs/adr/0004-track-a-pods-and-net1.md section 2.4)? On a running lab with Kubernetes and the addons.
#
#   probe-boot-window.sh layout stock|lab          install Cilium and Multus with those CNI paths, print the directories
#   probe-boot-window.sh delay <seconds>|off       start the Multus daemon that much later, through one more init container
#   probe-boot-window.sh keepers <label> [rounds]  pods that already run on worker2 when it reboots
#   probe-boot-window.sh waiters <label> [rounds]  an ordinary pod that waits for worker2 while it is down
#   probe-boot-window.sh summary                   one row per label and kind of pod, from the files in OUT
#
# stock: Cilium's cni.confPath /etc/cni/net.d and Multus's multusAutoconfigDir /host/etc/cni/net.d, the defaults of both
# charts, from a temporary copy of helm/ so that no tracked file changes. lab: the values of helm/ and make cni-migrate.
# Each round reboots worker2 and adds one row per pod to <out>/<label>-<keepers|waiters>.tsv: whether the pod has net1,
# whether its network-status annotation is absent, stale (its eth0 address is not the pod's address) or current, and the
# seconds after the boot of the pod's first and last RunPodSandbox and of the birth of 00-multus.conf.
# OUT names the output directory, .lab/boot-window/<UTC time> without it. LAB_DIR moves the guests' files, as for lab.sh.
# The scenario k8s-multus-net1 covers the third kind of pod, one created at boot that tolerates every NoSchedule taint.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
cd "$repo"
export LAB_PROFILE=lab PATH=$repo/.venv/bin:$PATH
out=${OUT:-$repo/.lab/boot-window/$(date -u +%Y%m%dT%H%M%SZ)}
mkdir -p "$out"
ns=net1-probe node=worker2 network=rdma-lab-net
multus_file=/etc/cni/net.d/00-multus.conf
before=""

lab() { "$here/lab.sh" "$@"; }
kc() { lab ssh cp1 "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf $*" < /dev/null; }
kc_stdin() { lab ssh cp1 "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf $*"; }
usage() { sed -n '6,10p' "${BASH_SOURCE[0]}" | sed 's/^# *//' >&2; exit 2; }
rollout() {
  local ds
  for ds in cilium kube-multus-ds; do kc "-n kube-system rollout status ds/$ds --timeout=300s"; done
}
init_containers() { kc "-n kube-system get ds kube-multus-ds -o jsonpath='{.spec.template.spec.initContainers[*].name}'"; }

cni_dirs() {
  local guest
  for guest in cp1 worker1 worker2; do
    printf '%s: ' "$guest"
    lab ssh "$guest" 'ls -1 /etc/cni/net.d | tr "\n" " "; echo; sudo cat /etc/cni/net.d/00-multus.conf; echo' < /dev/null
  done
}

layout() {
  local tmp
  case $1 in
    stock)
      tmp=$(mktemp -d)
      cp -a helm "$tmp/helm"
      sed -i 's|^  confPath: .*|  confPath: /etc/cni/net.d|' "$tmp/helm/cilium/values-lab.yaml"
      sed -i 's|^  multusAutoconfigDir: .*|  multusAutoconfigDir: /host/etc/cni/net.d|' "$tmp/helm/multus/values-lab.yaml"
      PYTHONPATH=scripts python - "$tmp/helm" <<'EOF'
import sys
from pathlib import Path

import addons.__main__ as addons

addons.HELM_DIR = Path(sys.argv[1])
sys.exit(addons.main(["apply", "--env", "lab", "--kubeconfig", ".lab/kubeconfig"]))
EOF
      rm -rf "$tmp"
      # The daemon reads its directory at start, and only a change of the DaemonSet restarts it.
      kc "-n kube-system rollout restart ds/kube-multus-ds"
      ;;
    lab)
      make --no-print-directory addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig
      make --no-print-directory cni-migrate
      ;;
    *) usage ;;
  esac
  rollout
  cni_dirs | tee "$out/layout-$1-cni-dirs.txt"
}

# One more init container that sleeps, so the daemon writes 00-multus.conf that much later after every start.
delay() {
  local -a names
  local i found="" patch
  [[ $1 == off || $1 =~ ^[0-9]+$ ]] || usage
  read -r -a names <<< "$(init_containers)"
  for i in "${!names[@]}"; do [[ ${names[$i]} == rfl-delay ]] && found=$i; done
  if [[ -n $found ]]; then
    patch=$(printf '[{"op":"remove","path":"/spec/template/spec/initContainers/%s"}]' "$found")
    kc "-n kube-system patch ds kube-multus-ds --type=json -p '$patch'"
  fi
  if [[ $1 != off ]]; then
    patch=$(printf '[{"op":"add","path":"/spec/template/spec/initContainers/-","value":{"name":"rfl-delay","image":"%s","command":["sleep","%s"]}}]' "$(image)" "$1")
    kc "-n kube-system patch ds kube-multus-ds --type=json -p '$patch'"
  fi
  rollout
  init_containers | tee "$out/delay-$1-init-containers.txt"
  echo
}

image() {
  python3 -c 'import yaml
c = yaml.safe_load(open("tracks/a-soft-roce/config/k8s-multus-net1.yaml"))
l = yaml.safe_load(open("versions.lock.yaml"))["components"][c["image"]]
print(l["ref"] + "@" + l["value"])'
}

# An ordinary pod at the PodSecurity restricted level with net1, which only the probed node can run.
pod_yaml() { # <name>
  cat <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: $1
  namespace: $ns
  annotations:
    k8s.v1.cni.cncf.io/networks: $network
spec:
  nodeSelector: {kubernetes.io/hostname: $node}
  terminationGracePeriodSeconds: 1
  automountServiceAccountToken: false
  enableServiceLinks: false
  containers:
    - name: probe
      image: $(image)
      command: ["sh", "-c", "while true; do sleep 3600; done"]
      securityContext:
        allowPrivilegeEscalation: false
        runAsNonRoot: true
        runAsUser: 65534
        runAsGroup: 65534
        capabilities: {drop: ["ALL"]}
        seccompProfile: {type: RuntimeDefault}
---
EOF
}

namespace() {
  local -a pair
  kc "get namespace $ns" > /dev/null 2>&1 && return 0
  read -r -a pair <<< "$(lab inventories)"
  kc "create namespace $ns"
  PYTHONPATH=scripts python -m run.multus render -i "${pair[0]}" -i "${pair[1]}" | sed "s/^  namespace: .*/  namespace: $ns/" | kc_stdin "apply -f -"
}

addresses() { kc "-n $ns exec $1 -- ip -o -4 addr show" 2> /dev/null | awk '$2 == "eth0" || $2 == "net1" {printf "%s=%s ", $2, $4}'; }
boot_id() { lab ssh "$node" 'cat /proc/sys/kernel/random/boot_id' < /dev/null 2> /dev/null || true; }

# reboot_start reboots the node, and reboot_wait returns once a boot other than $before answers.
reboot_start() {
  before=$(boot_id)
  lab ssh "$node" 'sudo systemctl reboot' < /dev/null > /dev/null 2>&1 || true
}
reboot_wait() {
  local now=""
  for _ in $(seq 1 400); do
    now=$(boot_id)
    [[ -n $now && $now != "$before" ]] && return 0
    sleep 1
  done
  echo "$node did not come back" >&2
  return 1
}

# The boot time, the birth of Multus's file and containerd's RunPodSandbox lines of this boot. A pod can be Ready before
# Multus writes its file, so this waits for the file first.
node_times() {
  lab ssh "$node" "for _ in \$(seq 1 180); do [ -e $multus_file ] && break; sleep 1; done; awk '/^btime/ {print \$2}' /proc/stat; stat -c %.W $multus_file; sudo journalctl -b -u containerd -o short-unix --no-pager | grep -F RunPodSandbox" < /dev/null
}

row() { # <kind> <label> <round> <raw dir> <pod> [extra column]
  python3 - "$@" <<'EOF'
import json
import sys
from datetime import datetime

kind, label, rnd, raw, pod = sys.argv[1:6]
lines = open(f"{raw}/round-{rnd}-node.txt").read().splitlines()
boot, born, journal = float(lines[0]), float(lines[1]), lines[2:]
items = {p["metadata"]["name"]: p for p in json.load(open(f"{raw}/round-{rnd}-pods.json"))["items"]}
p = items[pod]
mine = [line for line in journal if p["metadata"]["uid"] in line]
first = [float(line.split()[0]) for line in mine if "returns sandbox id" not in line]
done = [float(line.split()[0]) for line in mine if "returns sandbox id" in line]
addr = open(f"{raw}/round-{rnd}-{pod}-addr.txt").read().strip()
status = p["metadata"].get("annotations", {}).get("k8s.v1.cni.cncf.io/network-status")
networks = json.loads(status) if status else []
names = [n.get("interface") for n in networks]
eth0 = next((n.get("ips") or [None] for n in networks if n.get("interface") == "eth0"), [None])[0]
annotation = "absent" if status is None else "current" if eth0 == p["status"].get("podIP") else "stale"
cells = [label, rnd, pod, "yes" if "net1=" in addr else "no", addr, ",".join(n for n in names if n) or "none", annotation]
if kind == "waiters":
    stamp = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    node = json.load(open(f"{raw}/round-{rnd}-node.json"))
    ready = next(c for c in node["status"]["conditions"] if c["type"] == "Ready")
    scheduled = next(c for c in p["status"]["conditions"] if c["type"] == "PodScheduled")
    cells += [sys.argv[6], f"{stamp(ready['lastTransitionTime']) - boot:.0f}", f"{stamp(scheduled['lastTransitionTime']) - boot:.0f}"]
cells += [f"{first[0] - boot:.1f}" if first else "", f"{done[-1] - boot:.1f}" if done else "", f"{born - boot:.1f}"]
print("\t".join(cells))
EOF
}

summary() { # <kind> <label> <tsv> <what>
  awk -F'\t' -v l="$2" -v what="$4" 'NR > 1 && $1 == l { n++; if ($4 == "no") lost++ } END { printf "%s: %d of %d %s without net1\n", l, lost, n, what }' "$3" | tee "$out/$2-$1-summary.txt"
}

keepers() {
  local label=$1 rounds=${2:-5} raw=$out/$1-keepers tsv=$out/$1-keepers.tsv pod round r
  local -a pods=(keeper-1 keeper-2 keeper-3)
  local -A count
  mkdir -p "$raw"
  namespace
  kc "-n $ns delete pod ${pods[*]} --ignore-not-found --wait=true"
  for pod in "${pods[@]}"; do pod_yaml "$pod"; done | kc_stdin "apply -f -"
  kc "-n $ns wait --for=condition=Ready pod ${pods[*]} --timeout=180s"
  restarts() { kc "-n $ns get pod $1 -o jsonpath='{.status.containerStatuses[0].restartCount}/{.status.containerStatuses[0].ready}'"; }
  [[ -s $tsv ]] || printf 'layout\tround\tpod\thas_net1\taddresses\tnetwork_status\tannotation\tsandbox_first_s\tsandbox_done_s\tmultus_file_s\n' > "$tsv"
  for pod in "${pods[@]}"; do printf '%s before the first reboot: %s\n' "$pod" "$(addresses "$pod")"; done | tee "$raw/before.txt"
  for round in $(seq 1 "$rounds"); do
    for pod in "${pods[@]}"; do r=$(restarts "$pod"); count[$pod]=${r%/*}; done
    reboot_start
    reboot_wait
    # The kubelet starts each pod again in a new sandbox, which shows as one more restart of its container.
    for pod in "${pods[@]}"; do
      for _ in $(seq 1 300); do
        r=$(restarts "$pod" 2> /dev/null || true)
        [[ ${r#*/} == true && ${r%/*} -gt ${count[$pod]} ]] && break
        sleep 1
      done
      [[ ${r#*/} == true && ${r%/*} -gt ${count[$pod]} ]] || { echo "round $round: $pod did not restart: $r" >&2; return 1; }
    done
    node_times > "$raw/round-$round-node.txt"
    kc "-n $ns get pods -o json" > "$raw/round-$round-pods.json"
    for pod in "${pods[@]}"; do
      addresses "$pod" > "$raw/round-$round-$pod-addr.txt"
      row keepers "$label" "$round" "$raw" "$pod" | tee -a "$tsv"
    done
  done
  summary keepers "$label" "$tsv" "restarted pods came back"
}

waiters() {
  local label=$1 rounds=${2:-5} raw=$out/$1-waiters tsv=$out/$1-waiters.tsv pod round taints
  mkdir -p "$raw"
  namespace
  [[ -s $tsv ]] || printf 'layout\tround\tpod\thas_net1\taddresses\tnetwork_status\tannotation\ttaints_when_created\tnode_ready_s\tscheduled_s\tsandbox_first_s\tsandbox_done_s\tmultus_file_s\n' > "$tsv"
  for round in $(seq 1 "$rounds"); do
    pod=waiter-$round
    kc "-n $ns delete pod $pod --ignore-not-found --wait=true" > /dev/null
    reboot_start
    # The pod is created once the rebooting node carries a NoSchedule taint, so that only the node's return schedules it.
    taints=""
    for _ in $(seq 1 240); do
      taints=$(kc "get node $node -o jsonpath='{range .spec.taints[*]}{.key}:{.effect} {end}'" 2> /dev/null || true)
      [[ $taints == *NoSchedule* ]] && break
      sleep 0.5
    done
    pod_yaml "$pod" | kc_stdin "apply -f -" > /dev/null
    taints="${taints:-none}($(kc "-n $ns get pod $pod -o jsonpath='{.status.phase}'"))"
    reboot_wait
    kc "-n $ns wait --for=condition=Ready pod/$pod --timeout=300s" > /dev/null
    node_times > "$raw/round-$round-node.txt"
    kc "-n $ns get pods -o json" > "$raw/round-$round-pods.json"
    kc "get node $node -o json" > "$raw/round-$round-node.json"
    addresses "$pod" > "$raw/round-$round-$pod-addr.txt"
    row waiters "$label" "$round" "$raw" "$pod" "$taints" | tee -a "$tsv"
    kc "-n $ns delete pod $pod --wait=true" > /dev/null
  done
  summary waiters "$label" "$tsv" "pods that waited for the node came up"
}

# Label, kind of pod, pod starts on the rebooted node (one per row: a keeper once per round, a waiter once), how many
# came without net1, and the ranges of the first sandbox and of Multus's file.
summarize() {
  python3 - "$out" <<'EOF'
import csv
import sys
from pathlib import Path

out = Path(sys.argv[1])
span = lambda values: f"{min(values):.1f}-{max(values):.1f}" if values else ""
print("\t".join(["label", "kind", "pod_starts", "without_net1", "of_them_annotation_absent_or_stale", "with_net1_and_annotation_not_current",
                 "sandbox_first_s", "multus_file_s"]))
for kind in ("keepers", "waiters"):
    for path in sorted(out.glob(f"*-{kind}.tsv")):
        rows = list(csv.DictReader(path.open(), delimiter="\t"))
        first = [float(r["sandbox_first_s"]) for r in rows if r["sandbox_first_s"]]
        born = [float(r["multus_file_s"]) for r in rows]
        lost = [r for r in rows if r["has_net1"] == "no"]
        flagged = lambda some: str(sum(r["annotation"] != "current" for r in some)) if rows and "annotation" in rows[0] else ""
        print("\t".join([path.name.removesuffix(f"-{kind}.tsv"), kind, str(len(rows)), str(len(lost)), flagged(lost),
                         flagged([r for r in rows if r["has_net1"] == "yes"]), span(first), span(born)]))
EOF
}

case ${1:-} in
  layout) [[ $# -eq 2 ]] || usage; layout "$2" ;;
  delay) [[ $# -eq 2 ]] || usage; delay "$2" ;;
  keepers | waiters) [[ $# -ge 2 && $# -le 3 ]] || usage; "$@" ;;
  summary) [[ $# -eq 1 ]] || usage; summarize | tee "$out/summary.tsv" ;;
  *) usage ;;
esac
printf 'out: %s\n' "$out"
