#!/usr/bin/env bash
# NetBox from wiped guests (ADR 0008): the guest playbook installs and starts NetBox and changes nothing on a second
# run, NetBox differs from the model before the first sync, cel1-netbox passes with that first sync, and NetBox still
# equals the model after the guest powers off and boots again. Logs land in .lab/acceptance-netbox/<UTC time>/NN-<step>.log
# with steps.tsv and runs.tsv. LAB_DIR moves the guest's disk, as for lab.sh. The guest is stopped at the end.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
out=$repo/.lab/acceptance-netbox/$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$out"
cd "$repo"
export LAB_PROFILE=netbox
export LAB_DIR=${LAB_DIR:-$repo/.lab/netbox}
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

last_log() { local logs=("$out"/*-"$1".log); printf '%s\n' "${logs[-1]}"; }
run_id_of() { grep -oE 'results/[0-9]{8}T[0-9]{6}Z' "$(last_log "$1")" | head -n1 | cut -d/ -f2; }
unchanged() {
  awk '/^PLAY RECAP/ { recap = 1; hosts = 0; bad = 0; next }
       recap && /changed=/ { hosts++; if ($0 !~ /changed=0 /) bad++ }
       END { printf "%d hosts, %d with changes\n", hosts, bad; exit (hosts == 0 || bad) }' "$(last_log "$1")"
}
first_sync_created_all() { grep -E 'sync-makes-netbox-hold-the-model: passed - applied \{.create.: 1142, .update.: 0, .delete.: 0\}' "$(last_log "$1")"; }
power_cycle() { "$here/lab.sh" stop netbox1 && "$here/lab.sh" start netbox1; }

step lab-down 0 make lab-down
step lab-wipe 0 make lab-wipe
step netbox-render 0 make netbox-render
step netbox-guest 0 make netbox-guest
step netbox-guest-again 0 make netbox-guest
step netbox-guest-again-unchanged 0 unchanged netbox-guest-again
step diff-before-sync nonzero make netbox-diff
step cel1-netbox 0 make netbox-test
step first-sync-created-all 0 first_sync_created_all cel1-netbox
step diff-after-run 0 make netbox-diff
step topology-after-run 0 make netbox-topology
step power-cycle 0 power_cycle
step netbox-guest-after-boot 0 make netbox-guest
step netbox-guest-after-boot-unchanged 0 unchanged netbox-guest-after-boot
step diff-after-boot 0 make netbox-diff
step topology-after-boot 0 make netbox-topology
step validate 0 make validate
step lab-down-end 0 make lab-down

printf 'cel1-netbox\t%s\n' "$(run_id_of cel1-netbox)" > "$out/runs.tsv"
printf '\nsteps: %s\nruns:  %s\n' "$out/steps.tsv" "$out/runs.tsv"
awk -F'\t' 'NR > 1 && (($2 == "0" && $3 != 0) || ($2 == "nonzero" && $3 == 0)) { bad++ } END { print (bad ? bad : 0) " unexpected exit codes"; exit (bad ? 1 : 0) }' "$out/steps.tsv"
