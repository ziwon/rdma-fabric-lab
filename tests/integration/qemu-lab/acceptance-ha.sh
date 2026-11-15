#!/usr/bin/env bash
# Fleet-shaped proof on the ha profile, from wiped guests: three control planes joined behind a
# kube-vip VIP, a second run that changes nothing, Cilium, and the loss of the VIP holder.
# Logs land in .lab/acceptance-ha/<UTC time>/ with steps.tsv. The guests are stopped at the end.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
out=$repo/.lab/acceptance-ha/$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$out"
cd "$repo"
export LAB_PROFILE=ha
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
  printf '%02d %-34s expect %-7s exit %-3s %s\n' "$n" "$name" "$expect" "$rc" "$verdict"
}

last_log() { local logs=("$out"/*-"$1".log); printf '%s\n' "${logs[-1]}"; }

unchanged() {
  awk '/^PLAY RECAP/ { recap = 1; hosts = 0; bad = 0; next }
       recap && /changed=/ { hosts++; if ($0 !~ /changed=0 /) bad++ }
       END { printf "%d hosts, %d with changes\n", hosts, bad; exit (hosts == 0 || bad) }' "$(last_log "$1")"
}

joins() { grep -A3 -E 'TASK \[kubeadm : (Initialize the first control plane|Join the cluster)' "$(last_log k8s)" | grep -E '^(TASK|changed|ok|skipping)'; }

step lab-down 0 make lab-down
step lab-wipe 0 make lab-wipe
step lab-up 0 make lab-up
step inventory-check-ha 0 make inventory-check INVENTORY=tests/integration/qemu-lab/profiles/ha/inventory
step k8s 0 make k8s
step init-and-joins 0 joins
step k8s-again 0 make k8s
step k8s-again-unchanged 0 unchanged k8s-again
step addons 0 make addons ENV=lab KUBECONFIG_FILE=.lab/ha/kubeconfig
step k8s-ha-vip 0 make k8s-ha-test
step lab-down-end 0 make lab-down

printf 'k8s-ha-vip\t%s\n' "$(grep -oE 'results/[0-9]{8}T[0-9]{6}Z' "$(last_log k8s-ha-vip)" | head -n1 | cut -d/ -f2)" > "$out/runs.tsv"
printf '\nsteps: %s\nruns:  %s\n' "$out/steps.tsv" "$out/runs.tsv"
awk -F'\t' 'NR > 1 && (($2 == "0" && $3 != 0) || ($2 == "nonzero" && $3 == 0)) { bad++ } END { print (bad ? bad : 0) " unexpected exit codes"; exit (bad ? 1 : 0) }' "$out/steps.tsv"
