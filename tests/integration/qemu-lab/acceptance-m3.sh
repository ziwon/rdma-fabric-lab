#!/usr/bin/env bash
# M3 acceptance on the QEMU harness lab profile, from wiped guests to validated runs: RXE, Kubernetes,
# Cilium, Multus and Whereabouts with Multus's file alone in /etc/cni/net.d, the CNI migration as a no-op,
# the RDMA test image built in a wiped builder guest, both M3 scenarios,
# the M1 and M2 scenarios on the same guests in alternation, with net1 pods on the lab NIC during the RXE
# runs, and every idempotency rerun. Steps land in .lab/acceptance-m3/<UTC time>/NN-<step>.log, with
# steps.tsv and runs.tsv. LAB_DIR moves the lab guests' files, as for lab.sh. The builder guest lives in
# BUILDER_LAB_DIR, by default next to them, never in the Track B guest's own directory.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
out=$repo/.lab/acceptance-m3/$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$out"
cd "$repo"
export LAB_PROFILE=lab PATH=$repo/.venv/bin:$PATH
export BUILDER_LAB_DIR=${BUILDER_LAB_DIR:-${LAB_DIR:-$repo/.lab}/builder}
[[ $(realpath -m "$BUILDER_LAB_DIR") != "$(realpath -m "$repo/.lab/ibsim")" ]] \
  || { echo "BUILDER_LAB_DIR is the Track B guest's directory. Choose another." >&2; exit 2; }
n=0
printf 'step\texpect\texit\tlog\n' > "$out/steps.tsv"

lab() { "$here/lab.sh" "$@"; }
builder() { env LAB_PROFILE=ibsim LAB_DIR="$BUILDER_LAB_DIR" make "$@"; }
kc() { lab ssh cp1 "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf $*"; }

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

last_log() { local logs=("$out"/[0-9][0-9]-"$1".log); printf '%s\n' "${logs[-1]}"; }
run_id_of() { grep -oE 'results/[0-9]{8}T[0-9]{6}Z' "$(last_log "$1")" | head -n1 | cut -d/ -f2; }

# Every host of the last PLAY RECAP in the log of step $1 reports changed=0.
unchanged() {
  awk '/^PLAY RECAP/ { recap = 1; hosts = 0; bad = 0; next }
       recap && /changed=/ { hosts++; if ($0 !~ /changed=0 /) bad++ }
       END { printf "%d hosts, %d with changes\n", hosts, bad; exit (hosts == 0 || bad) }' "$(last_log "$1")"
}

# Every lab release reports unchanged in the log of step $1.
releases_unchanged() {
  local release ok=0
  for release in cilium multus whereabouts; do
    grep -E "^$release: unchanged, chart .* at revision 1$" "$(last_log "$1")" || { echo "$release did not stay unchanged"; ok=1; }
  done
  return "$ok"
}

one_revision_each() {
  local release
  for release in cilium multus whereabouts; do
    .tools/bin/helm history "$release" -n kube-system --kubeconfig .lab/kubeconfig -o json \
      | python3 -c 'import json, sys; h = json.load(sys.stdin); print(sys.argv[1], [r["revision"] for r in h]); sys.exit([r["revision"] for r in h] != [1])' "$release" \
      || return 1
  done
}

read -r -a pair <<< "$(lab inventories)"
hold_id=""
# Two net1 pods on the lab NIC while the RXE scenarios run, so RXE shares lab0 with macvlan children.
net1_hold() {
  local ns
  hold_id=$(date -u +%Y%m%dT%H%M%SZ)
  ns=$(ansible-inventory -i "${pair[0]}" -i "${pair[1]}" --host worker1 | python3 -c 'import json, sys; print(json.load(sys.stdin)["lab_k8s_namespace"])')
  PYTHONPATH=scripts python -m run.multus render --with-pods "$hold_id" -i "${pair[0]}" -i "${pair[1]}" | kc "apply -f -"
  kc "-n $ns wait --for=condition=Ready pod --all --timeout=180s" < /dev/null
}
net1_release() { make cleanup RUN="$hold_id"; }
# A rebooted node's Cilium agent turns Ready at its readiness probe's next tick, up to 30 s after it is healthy. So after
# a step that reboots workers the run waits for the rollouts, as acceptance-m2.sh does after it deletes an agent.
settled() {
  kc "-n kube-system rollout status daemonset/cilium --timeout=300s" < /dev/null \
    && kc "-n kube-system rollout status daemonset/kube-multus-ds --timeout=300s" < /dev/null \
    && kc "wait --for=condition=Ready node --all --timeout=300s" < /dev/null
}
cni_dirs() {
  local d
  for g in cp1 worker1 worker2; do
    echo "== $g"
    for d in /etc/cni/net.d "$(python3 -c 'import yaml; print(yaml.safe_load(open("helm/cilium/values-lab.yaml"))["cni"]["confPath"])')"; do
      lab ssh "$g" "echo '-- $d'; sudo ls -1A $d; sudo cat $d/00-multus.conf 2>/dev/null; echo" < /dev/null
    done
  done
}
gids() { for w in worker1 worker2; do echo "== $w"; lab ssh "$w" 'cat /sys/class/infiniband/*/ports/1/gid_attrs/ndevs/* 2>/dev/null | sort | uniq -c' < /dev/null; done; }
# RXE numbers are collected with no other guest competing: every QEMU process must be a lab profile guest.
only_lab_guests() {
  local others
  pgrep -af '^qemu-system-x86_64 ' | sed -E "s/ -drive .*//" || true
  others=$(pgrep -af '^qemu-system-x86_64 ' | grep -vE -- '-name (cp1|worker1|worker2),process=rfl-lab-' || true)
  [[ -z $others ]] || { echo "other guests run: $others"; return 1; }
}

step lab-down 0 make lab-down
step builder-down 0 builder lab-down
step lab-wipe 0 make lab-wipe
step builder-wipe 0 builder lab-wipe
step lab-up 0 make lab-up
step versions-m3 0 make versions SCOPE=m3
step bootstrap 0 make bootstrap
step rxe-up 0 make rxe-up
step k8s 0 make k8s
step addons-template 0 make addons-template ENV=lab
step addons 0 make addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig
step cni-dirs 0 cni_dirs
step cni-migrate 0 make cni-migrate
step cni-migrate-unchanged 0 unchanged cni-migrate
step rdma-tools-image 0 make rdma-tools-image
step builder-down 0 builder lab-down
step rdma-tools-load 0 make rdma-tools-load
step guests-before-k8s-hostnet-rdma 0 only_lab_guests
step k8s-hostnet-rdma 0 make k8s-hostnet-test
step k8s-multus-net1 0 make k8s-multus-test
step settled-after-k8s-multus-net1 0 settled
step k8s-baseline 0 make k8s-test
step guests-before-vm-rxe-baseline 0 only_lab_guests
step vm-rxe-baseline 0 make rxe-test
step net1-hold 0 net1_hold
step gids-with-net1 0 gids
step guests-before-vm-rxe-baseline-net1-held 0 only_lab_guests
step vm-rxe-baseline-net1-held 0 make rxe-test
step vm-rxe-rerun-net1-held 0 make rxe-test SCENARIO=vm-rxe-rerun
step net1-release 0 net1_release
step guests-before-vm-rxe-reboot 0 only_lab_guests
step vm-rxe-reboot 0 make rxe-test SCENARIO=vm-rxe-reboot
step settled-after-vm-rxe-reboot 0 settled
step k8s-baseline-after-reboot 0 make k8s-test
step guests-before-k8s-hostnet-rdma-after-reboot 0 only_lab_guests
step k8s-hostnet-rdma-after-reboot 0 make k8s-hostnet-test
step k8s-again 0 make k8s
step k8s-again-unchanged 0 unchanged k8s-again
step addons-again 0 make addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig
step addons-again-unchanged 0 releases_unchanged addons-again
step helm-history-one-revision 0 one_revision_each
step rdma-tools-load-again 0 make rdma-tools-load
step rdma-tools-load-again-unchanged 0 unchanged rdma-tools-load-again
step validate 0 make validate
step report 0 make report

{
  for s in k8s-hostnet-rdma k8s-multus-net1 k8s-baseline vm-rxe-baseline vm-rxe-baseline-net1-held vm-rxe-rerun-net1-held \
    vm-rxe-reboot k8s-baseline-after-reboot k8s-hostnet-rdma-after-reboot; do
    printf '%s\t%s\n' "$s" "$(run_id_of "$s")"
  done
} > "$out/runs.tsv"
printf '\nsteps: %s\nruns:  %s\n' "$out/steps.tsv" "$out/runs.tsv"
awk -F'\t' 'NR > 1 && (($2 == "0" && $3 != 0) || ($2 == "nonzero" && $3 == 0)) { bad++ } END { print (bad ? bad : 0) " unexpected exit codes"; exit (bad ? 1 : 0) }' "$out/steps.tsv"
