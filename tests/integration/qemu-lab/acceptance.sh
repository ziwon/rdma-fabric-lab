#!/usr/bin/env bash
# RXE acceptance on the QEMU harness, from wiped guests to validated runs: doctor, the lock, preflight,
# bootstrap, rxe-up, the RXE scenarios, their faults, cleanup and reset-lab.
# Every step's output lands in .lab/acceptance/<UTC time>/NN-<step>.log and its exit
# code in steps.tsv next to the exit code the step should have.
# It deletes the harness guests first, so it never runs against anything else.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
out=$repo/.lab/acceptance/$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$out"
cd "$repo"
n=0
printf 'step\texpect\texit\tlog\n' > "$out/steps.tsv"

lab() { "$here/lab.sh" "$@"; }

# step <name> <expect: 0|nonzero> <command...>
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
  printf '%02d %-34s expect %-7s exit %-3s %s\n' "$n" "$name" "$expect" "$rc" "$verdict"
}

run_id_of() {
  local logs=("$out"/*-"$1".log)
  grep -oE 'results/[0-9]{8}T[0-9]{6}Z' "${logs[-1]}" | head -n1 | cut -d/ -f2
}

both() { lab ssh worker1 "$@" < /dev/null && lab ssh worker2 "$@" < /dev/null; }

# NIC name of a guest as the harness wrote it into the override inventory.
nic() { awk -v h="$1:" -v k="$2:" '$1 == h { f = 1 } f && $1 == k { print $2; exit }' "$repo/.lab/inventory.yml"; }

# Every process that carries a run marker, with the marker, then the run directories.
# shellcheck disable=SC2016 # expands inside the guest
scan='for p in /proc/[0-9]*; do m=$(tr "\0" "\n" 2>/dev/null < $p/environ | grep "^RFL_RUN="); [ -n "$m" ] && echo "${p#/proc/} $m $(tr "\0" " " < $p/cmdline)"; done; ls /var/tmp/rfl 2>&1; true'
scan_both() { both "sudo sh -c $(printf '%q' "$scan"); $*"; }

overlap_plan=$out/plan-overlapping-docker0.yml
sed -e 's#rdma: 10.77.0.0/24#rdma: 172.17.0.0/24#' -e 's#multus_pool: 10.77.0.100-10.77.0.199#multus_pool: 172.17.0.100-172.17.0.199#' \
  ansible/inventories/lab/group_vars/all/lab.yml > "$overlap_plan"

step lab-down 0 make lab-down
step lab-wipe 0 make lab-wipe
step lab-up 0 make lab-up
w1_lab=$(nic worker1 lab_rdma_nic)
step doctor 0 make doctor
step doctor-overlapping-plan nonzero make doctor LAB_PLAN="$overlap_plan"
step versions-lab 0 make versions SCOPE=lab
step preflight-before-bootstrap nonzero make preflight
step bootstrap 0 make bootstrap
step preflight 0 make preflight
step rxe-up 0 make rxe-up
step rxe-test-baseline 0 make rxe-test
step bootstrap-again 0 make bootstrap
step rxe-up-again 0 make rxe-up
step rxe-test-rerun 0 make rxe-test SCENARIO=vm-rxe-rerun
step rxe-test-reboot 0 make rxe-test SCENARIO=vm-rxe-reboot

step break-disable-rfl-rxe 0 both sudo systemctl disable rfl-rxe.service
step rxe-test-reboot-unit-disabled nonzero make rxe-test SCENARIO=vm-rxe-reboot
step restore-rxe-up 0 make rxe-up
step rxe-test-after-restore 0 make rxe-test

step fault-worker1-lab-link-down 0 lab ssh worker1 sudo ip link set dev "$w1_lab" down
step rxe-test-link-down nonzero make rxe-test
step restore-worker1-lab-link 0 lab ssh worker1 "sudo ip link set dev $w1_lab up && sleep 5 && ip -br addr show dev $w1_lab"
step rxe-test-link-restored 0 make rxe-test

step fault-worker1-egress-loss 0 lab ssh worker1 sudo tc qdisc add dev "$w1_lab" root netem loss 100%
step rxe-test-egress-loss nonzero make rxe-test
step restore-worker1-qdisc 0 lab ssh worker1 sudo tc qdisc del dev "$w1_lab" root
step rxe-test-loss-restored 0 make rxe-test

# A decoy process carrying another run id must survive the cleanup of the crashed run.
decoy=20991231T235959Z
step decoy-other-run 0 lab ssh worker1 "mkdir -p /var/tmp/rfl/$decoy && cd /var/tmp/rfl/$decoy && { env RFL_RUN=$decoy setsid sleep 900 < /dev/null > /dev/null 2>&1 & }"
crash_log=$out/crash-runner.log
setsid make rxe-test > "$crash_log" 2>&1 &
crash_pgid=$!
for _ in $(seq 1 60); do
  grep -q 'rping-verified: ' "$crash_log" 2> /dev/null && break
  sleep 1
done
step crash-runner-with-sigkill 0 kill -KILL -- "-$crash_pgid"
crash_run=$(grep -oE 'results/[0-9]{8}T[0-9]{6}Z' "$crash_log" | head -n1 | cut -d/ -f2)
step leftovers-before-cleanup 0 scan_both
step cleanup-crashed-run 0 make cleanup RUN="$crash_run"
step leftovers-after-cleanup 0 scan_both rdma link show
step guests-still-running 0 make lab-status
step cleanup-decoy-run 0 make cleanup RUN="$decoy"
step cleanup-without-run nonzero make cleanup

step reset-lab-without-confirm nonzero make reset-lab
step reset-playbook-without-confirm nonzero .venv/bin/ansible-playbook -i ansible/inventories/lab -i .lab/inventory.yml ansible/playbooks/reset.yml
step device-after-refused-reset 0 both 'rdma link show; ip -br -4 addr show; systemctl is-active rfl-rxe.service'

step collect 0 make collect
step validate 0 make validate
step report 0 make report

{
  printf 'baseline\t%s\n' "$(run_id_of rxe-test-baseline)"
  printf 'rerun\t%s\n' "$(run_id_of rxe-test-rerun)"
  printf 'reboot\t%s\n' "$(run_id_of rxe-test-reboot)"
  printf 'reboot-unit-disabled\t%s\n' "$(run_id_of rxe-test-reboot-unit-disabled)"
  printf 'link-down\t%s\n' "$(run_id_of rxe-test-link-down)"
  printf 'link-restored\t%s\n' "$(run_id_of rxe-test-link-restored)"
  printf 'egress-loss\t%s\n' "$(run_id_of rxe-test-egress-loss)"
  printf 'loss-restored\t%s\n' "$(run_id_of rxe-test-loss-restored)"
  printf 'crashed\t%s\n' "$crash_run"
  printf 'collect\t%s\n' "$(run_id_of collect)"
} > "$out/runs.tsv"
printf '\nsteps: %s\nruns:  %s\n' "$out/steps.tsv" "$out/runs.tsv"
awk -F'\t' 'NR > 1 && (($2 == "0" && $3 != 0) || ($2 == "nonzero" && $3 == 0)) { bad++ } END { print (bad ? bad : 0) " unexpected exit codes"; exit (bad ? 1 : 0) }' "$out/steps.tsv"
