#!/usr/bin/env bash
# shellcheck disable=SC2016 # single-quoted commands expand inside the guests
# make cleanup on runs killed with SIGKILL, so no runner could clean up after itself. Each track first starts a
# bystander run and kills it, then target runs that it kills, then cleans the targets one by one. Every target's
# resources must be gone, the bystander's must stay until its own cleanup, and the rest of the profile must
# equal the snapshot taken before the first run. Steps land in .lab/cleanup-check/<UTC time>-<track>/ with
# steps.tsv. The profile must be up: lab with Kubernetes, the addons and the RDMA test image for a, the Track B
# guest with its image for b, the Track C guest with its image for c.
#
#   cleanup-check.sh a|b|c      LAB_DIR moves the profile's guest files, as for lab.sh
#   cleanup-check.sh reset      make reset-lab refuses without CONFIRM=lab, removes rxe0 with it, and
#                               make bootstrap rxe-up and vm-rxe-baseline bring the lab back
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
track=${1:?usage: cleanup-check.sh a|b|c|reset}
case $track in
  a | reset) export LAB_PROFILE=lab ;;
  b) export LAB_PROFILE=ibsim ;;
  c) export LAB_PROFILE=ns3 ;;
  *) echo "usage: cleanup-check.sh a|b|c|reset" >&2; exit 2 ;;
esac
out=$repo/.lab/cleanup-check/$(date -u +%Y%m%dT%H%M%SZ)-$track
mkdir -p "$out"
cd "$repo"
export PATH=$repo/.venv/bin:$PATH PYTHONPATH=$repo/scripts
read -r -a pair <<< "$("$here/lab.sh" inventories)"
inventories=(-i "${pair[0]}" -i "${pair[1]}")
n=0
printf 'step\texpect\texit\tlog\n' > "$out/steps.tsv"

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
  printf '%02d %-40s expect %-7s exit %-3s %s\n' "$n" "$name" "$expect" "$rc" "$verdict"
}

lab() { "$here/lab.sh" "$@"; }
gsh() { lab ssh "$1" "$2" < /dev/null; }

new_run_id() {
  local id
  id=$(date -u +%Y%m%dT%H%M%SZ)
  sleep 1
  printf '%s\n' "$id"
}

# start <name> <command...>: the command in its own process group. Its pid, which is the group id, goes to <name>.pid.
start() {
  local name=$1
  shift
  setsid "$@" > "$out/$name.out" 2>&1 < /dev/null &
  printf '%s\n' "$!" > "$out/$name.pid"
  echo "started $name, pid $!: $*"
}

pid_of() { cat "$out/$1.pid"; }

# wait_for <seconds> <command...>: poll the command every 2 s until it succeeds.
wait_for() {
  local deadline=$((SECONDS + $1))
  shift
  until "$@"; do
    ((SECONDS < deadline)) || { echo "still false after the deadline: $*"; return 1; }
    sleep 2
  done
}

kill_group() {
  local pid
  pid=$(pid_of "$1")
  kill -KILL -- "-$pid" 2> /dev/null || true
  echo "SIGKILL to process group $pid of $1"
}

kill_runner() {
  local pid
  pid=$(pid_of "$1")
  kill -KILL "$pid"
  echo "SIGKILL to runner $pid of $1 alone; its children stay"
}

cleanup_run() { make --no-print-directory cleanup RUN="$1"; }

snapshot_rest() { rest > "$out/rest-before.txt"; cat "$out/rest-before.txt"; }
same_as_before() { diff -u "$out/rest-before.txt" <(rest) && echo "the rest of the profile equals the snapshot from before the runs"; }

# ---------------------------------------------------------------- Track A

kc() { gsh cp1 "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf $*"; }
run_processes_command() { python -c 'import sys; from run.remote import find_run_processes_command as f; print(f(sys.argv[1]))' "$1"; }
guest_processes() { gsh "$1" "sudo sh -c $(printf '%q' "$(run_processes_command "$2")")"; }
has_processes() { [[ -n $(guest_processes "$1" "$2") ]]; }
rxe_processes_up() { has_processes worker1 "$1" || has_processes worker2 "$1"; }
no_processes_or_dir() {
  local w left=0
  for w in worker1 worker2; do
    echo "== $w"
    guest_processes "$w" "$1" | tee "$out/left-$w-$1.txt"
    [[ ! -s $out/left-$w-$1.txt ]] || left=1
    if gsh "$w" "test -d /var/tmp/rfl/$1"; then echo "/var/tmp/rfl/$1 still present"; left=1; else echo "/var/tmp/rfl/$1 absent"; fi
  done
  return "$left"
}
processes_and_dir() {
  local w
  for w in worker1 worker2; do echo "== $w"; guest_processes "$w" "$1"; gsh "$w" "ls -A /var/tmp/rfl/$1 2>&1"; done
  has_processes worker1 "$1" || has_processes worker2 "$1" || gsh worker1 "test -d /var/tmp/rfl/$1" || gsh worker2 "test -d /var/tmp/rfl/$1"
}
labeled() { kc "get namespace,pods,network-attachment-definitions -A -l rdma-fabric-lab/run=$1 -o name"; }
namespace_holds() { # <namespace> <run-id>: the run's namespace holds its pods, and the attachment when it is the lab namespace
  local held
  held=$(kc "-n $1 get pods,network-attachment-definitions -o name")
  echo "$held"
  kc "get namespace -l rdma-fabric-lab/run=$2 -o name" | grep -qx "namespace/$1" && grep -q '^pod/' <<< "$held"
}
lab_namespace_ready() { namespace_holds rdma-lab "$1" > /dev/null && grep -q networkattachmentdefinition <(kc "-n rdma-lab get network-attachment-definitions -o name"); }
gone() { [[ -z $(labeled "$1") ]] && ! kc "get namespace $2" > /dev/null 2>&1 && echo "nothing labeled with run $1 and no namespace $2"; }

rest_a() {
  local w
  for w in worker1 worker2; do
    echo "== $w"
    gsh "$w" 'rdma -j link show' | python -c 'import json, sys; print(sorted((l["ifname"], l.get("netdev"), l["state"]) for l in json.load(sys.stdin)))'
    gsh "$w" 'systemctl is-active rfl-rxe.service; ip -br -4 addr show lab0 | tr -s " "; ls -1A /var/tmp/rfl 2>/dev/null | sort'
  done
  echo "== cluster"
  kc 'get nodes --no-headers' | awk '{print $1, $2}'
  kc 'get namespaces -o name'
  kc 'get pods -A --no-headers' | awk '{print $1, $2, $3}' | sort
  kc 'get network-attachment-definitions -A -o name'
  kc 'get daemonsets,deployments -A -o name' | sort
  kc 'get ippools.whereabouts.cni.cncf.io -A -o json' | python -c 'import json, sys
for pool in json.load(sys.stdin)["items"]:
    print(pool["metadata"]["name"], sorted((k, v.get("podref")) for k, v in (pool["spec"].get("allocations") or {}).items()))'
}

check_a() {
  local x r m xns
  step rest-before 0 snapshot_rest
  x=$(new_run_id)
  xns=rfl-${x,,}
  step bystander-start 0 start bystander make --no-print-directory k8s-test RUN="$x"
  step bystander-pods-up 0 wait_for 300 namespace_holds "$xns" "$x"
  step bystander-killed 0 kill_group bystander
  r=$(new_run_id)
  step rxe-start 0 start rxe make --no-print-directory rxe-test RUN="$r"
  step rxe-processes-up 0 wait_for 180 rxe_processes_up "$r"
  step rxe-killed 0 kill_group rxe
  step rxe-left-behind 0 processes_and_dir "$r"
  m=$(new_run_id)
  step multus-start 0 start multus make --no-print-directory k8s-multus-test RUN="$m"
  step multus-nad-and-pods-up 0 wait_for 300 lab_namespace_ready "$m"
  step multus-killed 0 kill_group multus
  step multus-left-behind 0 namespace_holds rdma-lab "$m"
  step cleanup-rxe 0 cleanup_run "$r"
  step rxe-gone 0 no_processes_or_dir "$r"
  step multus-untouched-by-rxe-cleanup 0 namespace_holds rdma-lab "$m"
  step bystander-untouched-by-rxe-cleanup 0 namespace_holds "$xns" "$x"
  step cleanup-multus 0 cleanup_run "$m"
  step multus-gone 0 gone "$m" rdma-lab
  step multus-processes-gone 0 no_processes_or_dir "$m"
  step bystander-untouched-by-multus-cleanup 0 namespace_holds "$xns" "$x"
  step cleanup-bystander 0 cleanup_run "$x"
  step bystander-gone 0 gone "$x" "$xns"
  step rest-after 0 same_as_before
  printf 'bystander\t%s\nrxe\t%s\nmultus\t%s\n' "$x" "$r" "$m" > "$out/runs.tsv"
}

# ---------------------------------------------------------------- Track B and Track C

guest_name() { python -c 'import sys, yaml; print(next(iter(yaml.safe_load(open(sys.argv[1]))["all"]["children"].values()))["hosts"].popitem()[0])' "${pair[0]}/hosts.yml"; }
containers() { gsh "$guest" "sudo docker ps --all --filter label=rfl.track=$label --format '{{.Names}} {{.Label \"rfl.run\"}}'"; }
container_up() { containers | grep -q "^$1 "; }
of_run() { containers | awk -v r="$1-" 'index($2, r) == 1'; }
has_run() { [[ -n $(of_run "$1") ]] && of_run "$1"; }
run_gone() {
  [[ -z $(of_run "$1") ]] || { of_run "$1"; return 1; }
  ! gsh "$guest" "ls -1 $root/runs 2>/dev/null | grep -q '^$1-'" || { echo "a run directory of $1 remains"; return 1; }
  echo "no container and no run directory of $1"
}
host_simulators() { pgrep -a -x "$1" || true; }
no_host_simulator() { local found; found=$(for p in $simulators; do host_simulators "$p"; done); [[ -z $found ]] && echo "no ${simulators// /, } process on the host"; }

rest_bc() {
  gsh "$guest" "sudo docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' | sort; systemctl is-active docker; cat /etc/rfl/$marker"
  containers | awk '{print $1}' | sort
}

check_b() {
  local x t
  guest=$(guest_name) label=b-ibsim root=/var/tmp/rfl-track-b marker=track-b-guest simulators="ibsim opensm"
  step rest-before 0 snapshot_rest
  x=$(new_run_id)
  step bystander-start 0 start bystander python -m ibsim run --scenario b-04 "${inventories[@]}" --run-id "$x"
  step bystander-container-up 0 wait_for 180 container_up "rfl-ibsim-$x-b-04"
  step bystander-killed 0 kill_group bystander
  t=$(new_run_id)
  step track-b-start 0 start track-b make --no-print-directory track-b RUN="$t"
  step track-b-container-up 0 wait_for 300 container_up "rfl-ibsim-$t-b-01"
  step track-b-killed 0 kill_group track-b
  step track-b-left-behind 0 has_run "$t"
  step cleanup-track-b 0 cleanup_run "$t"
  step track-b-gone 0 run_gone "$t"
  step bystander-untouched 0 container_up "rfl-ibsim-$x-b-04"
  step cleanup-bystander 0 cleanup_run "$x"
  step bystander-gone 0 run_gone "$x"
  step no-simulator-on-host 0 no_host_simulator
  step rest-after 0 same_as_before
  printf 'bystander\t%s\ntrack-b\t%s\n' "$x" "$t" > "$out/runs.tsv"
}

decoy_up() { local pid; for pid in $(pgrep -x SimAI_simulator); do tr '\0' '\n' < "/proc/$pid/environ" | grep -x "RFL_RUN=$1" && return 0; done; return 1; }
no_decoy() { ! pgrep -ax SimAI_simulator && echo "no SimAI_simulator process on the host"; }
decoy_dir() { compgen -G "${TMPDIR:-/tmp}/rfl-ns3-decoy-$1-*"; }
no_decoy_dir() { ! decoy_dir "$1" && echo "no decoy directory of $1 in ${TMPDIR:-/tmp}"; }

check_c() {
  local x t h scenario=c-03-incast-simultaneous
  guest=$(guest_name) label=c-ns3 root=/var/tmp/rfl-track-c marker=track-c-guest simulators="SimAI_simulator rfl-star"
  step track-c-smoke 0 make --no-print-directory track-c-smoke
  step rest-before 0 snapshot_rest
  x=$(new_run_id)
  step bystander-start 0 start bystander python -m ns3 run --scenario "$scenario" "${inventories[@]}" --run-id "$x"
  step bystander-container-up 0 wait_for 180 has_run "$x"
  step bystander-killed 0 kill_group bystander
  t=$(new_run_id)
  step matrix-start 0 start matrix make --no-print-directory track-c-matrix RUN="$t"
  step matrix-container-up 0 wait_for 300 has_run "$t"
  step matrix-killed 0 kill_group matrix
  h=$(new_run_id)
  step decoy-break-start 0 start decoy python -m ns3 run --scenario "$scenario" "${inventories[@]}" --run-id "$h" --break host-process
  step decoy-break-container-up 0 wait_for 180 has_run "$h"
  step decoy-on-host 0 decoy_up "$h"
  step decoy-break-runner-killed 0 kill_runner decoy
  step decoy-outlives-its-runner 0 decoy_up "$h"
  step decoy-dir-left-behind 0 decoy_dir "$h"
  step cleanup-matrix 0 cleanup_run "$t"
  step matrix-gone 0 run_gone "$t"
  step decoy-break-untouched 0 has_run "$h"
  step decoy-untouched 0 decoy_up "$h"
  step bystander-untouched 0 has_run "$x"
  step cleanup-decoy-break 0 cleanup_run "$h"
  step decoy-break-gone 0 run_gone "$h"
  step decoy-gone 0 no_decoy
  step decoy-dir-gone 0 no_decoy_dir "$h"
  step bystander-still-untouched 0 has_run "$x"
  step cleanup-bystander 0 cleanup_run "$x"
  step bystander-gone 0 run_gone "$x"
  step no-simulator-on-host 0 no_host_simulator
  step rest-after 0 same_as_before
  printf 'bystander\t%s\nmatrix\t%s\ndecoy-break\t%s\n' "$x" "$t" "$h" > "$out/runs.tsv"
}

# ---------------------------------------------------------------- reset-lab and recovery

rxe_state() { local w; for w in worker1 worker2; do echo "== $w"; gsh "$w" 'rdma link show; ip -br -4 addr show lab0; systemctl is-enabled rfl-rxe.service 2>&1 || true'; done; }
rxe_present() { rxe_state | tee /dev/stderr | grep -c 'rxe0/1 state ACTIVE' | grep -qx 2; }
rxe_absent() { ! rxe_state | tee /dev/stderr | grep -q 'rxe0'; }
only_lab_guests() {
  local others
  pgrep -af '^qemu-system-x86_64 ' | sed -E 's/ -drive .*//' || true
  others=$(pgrep -af '^qemu-system-x86_64 ' | grep -vE -- '-name (cp1|worker1|worker2),process=rfl-lab-' || true)
  [[ -z $others ]] || { echo "other guests run: $others"; return 1; }
}
nodes_ready() { kc 'get nodes --no-headers' | tee /dev/stderr | awk '$2 != "Ready" { bad++ } END { exit bad > 0 }'; }

check_reset() {
  step rxe-before 0 rxe_present
  step reset-refused-without-confirm nonzero make --no-print-directory reset-lab
  step rxe-kept-after-refusal 0 rxe_present
  step reset-lab 0 make --no-print-directory reset-lab CONFIRM=lab
  step rxe-gone-after-reset 0 rxe_absent
  step cluster-kept-after-reset 0 nodes_ready
  step bootstrap 0 make --no-print-directory bootstrap
  step rxe-up 0 make --no-print-directory rxe-up
  step rxe-back 0 rxe_present
  step only-lab-guests 0 only_lab_guests
  step vm-rxe-baseline-after-recovery 0 make --no-print-directory rxe-test
}

rest() { if [[ $LAB_PROFILE == lab ]]; then rest_a; else rest_bc; fi; }

case $track in
  a) check_a ;;
  b) check_b ;;
  c) check_c ;;
  reset) check_reset ;;
esac
printf '\nsteps: %s\n' "$out/steps.tsv"
awk -F'\t' 'NR > 1 && (($2 == "0" && $3 != 0) || ($2 == "nonzero" && $3 == 0)) { bad++ } END { print (bad ? bad : 0) " unexpected exit codes"; exit (bad ? 1 : 0) }' "$out/steps.tsv"
