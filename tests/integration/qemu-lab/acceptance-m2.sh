#!/usr/bin/env bash
# M2 acceptance on the QEMU harness lab profile, from wiped guests to validated runs.
# Every step's output lands in .lab/acceptance-m2/<UTC time>/NN-<step>.log and its exit code
# in steps.tsv next to the exit code the step should have.
# It deletes the lab profile guests first, so it never runs against anything else.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
out=$repo/.lab/acceptance-m2/$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$out"
cd "$repo"
export LAB_PROFILE=lab
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

last_log() { local logs=("$out"/*-"$1".log); printf '%s\n' "${logs[-1]}"; }
run_id_of() { grep -oE 'results/[0-9]{8}T[0-9]{6}Z' "$(last_log "$1")" | head -n1 | cut -d/ -f2; }

# Every host of the last PLAY RECAP in the log of step $1 reports changed=0.
unchanged() {
  awk '/^PLAY RECAP/ { recap = 1; hosts = 0; bad = 0; next }
       recap && /changed=/ { hosts++; if ($0 !~ /changed=0 /) bad++ }
       END { printf "%d hosts, %d with changes\n", hosts, bad; exit (hosts == 0 || bad) }' "$(last_log "$1")"
}

kubectl_cp1() { lab ssh cp1 "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf $*" < /dev/null; }
one_revision() {
  .tools/bin/helm history cilium -n kube-system --kubeconfig .lab/kubeconfig -o json \
    | .venv/bin/python -c 'import json, sys; h = json.load(sys.stdin); print(h); sys.exit([r["revision"] for r in h] != [1])'
}

# The harness sockets are AF_UNIX only, and the switch runs without capabilities.
harness_sockets() {
  ss -xlp | grep -F "$repo/.lab/net/"
  grep -E '^Cap(Inh|Prm|Eff)' "/proc/$(cat .lab/net/mgmt/switch.pid)/status"
}

# sha256 of every release file the containerd role installed, and a byte comparison of each
# archive with the files unpacked from it, on every node.
# shellcheck disable=SC2016 # expands inside the guest
bytes='cd /var/cache/rfl/k8s && sha256sum *.tar.gz *.tgz /usr/local/sbin/runc && sudo tar -df containerd-*-linux-amd64.tar.gz -C /usr/local && sudo tar -df cni-plugins-linux-amd64-*.tgz -C /opt/cni/bin && echo "archives equal the unpacked files"'
installed_bytes() { for g in cp1 worker1 worker2; do echo "== $g"; lab ssh "$g" "$bytes" < /dev/null || return 1; done; }

vxlan_fault() { lab ssh worker1 "sudo iptables $1 INPUT -i \$(ip -o -4 addr show to 192.168.140.12 | awk '{print \$2}') -p udp --dport 8472 -m comment --comment rfl-fault -j DROP && sudo iptables -S INPUT" < /dev/null; }
delete_worker1_agent() { kubectl_cp1 "-n kube-system delete pod -l k8s-app=cilium --field-selector spec.nodeName=worker1 --wait=false"; }
agents_back() { kubectl_cp1 "-n kube-system rollout status daemonset/cilium --timeout=300s"; }

step lab-down 0 make lab-down
step lab-wipe 0 make lab-wipe
step lab-up 0 make lab-up
step harness-sockets 0 harness_sockets
step versions-m2 0 make versions SCOPE=m2
step bootstrap 0 make bootstrap
step rxe-up 0 make rxe-up
step k8s 0 make k8s
step kubeconfig-mode 0 stat -c '%a %n' .lab/kubeconfig
step installed-bytes 0 installed_bytes
step addons-template 0 make addons-template ENV=lab
step addons 0 make addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig
step k8s-baseline 0 make k8s-test
step k8s-again 0 make k8s
step k8s-again-unchanged 0 unchanged k8s-again
step addons-again 0 make addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig
step helm-history-one-revision 0 one_revision
step vm-rxe-baseline-on-k8s 0 make rxe-test
step inventory-check-lab 0 make inventory-check INVENTORY=lab

step break-delete-worker1-cilium-agent 0 delete_worker1_agent
step k8s-baseline-agent-deleted nonzero make k8s-test
step restore-cilium-agents 0 agents_back
step k8s-baseline-agents-back 0 make k8s-test

step break-drop-vxlan-on-worker1 0 vxlan_fault -I
step k8s-baseline-vxlan-dropped nonzero make k8s-test
step restore-vxlan-on-worker1 0 vxlan_fault -D
step k8s-baseline-vxlan-restored 0 make k8s-test

step validate 0 make validate
step report 0 make report

{
  printf 'k8s-baseline\t%s\n' "$(run_id_of k8s-baseline)"
  printf 'vm-rxe-baseline-on-k8s\t%s\n' "$(run_id_of vm-rxe-baseline-on-k8s)"
  printf 'agent-deleted\t%s\n' "$(run_id_of k8s-baseline-agent-deleted)"
  printf 'agents-back\t%s\n' "$(run_id_of k8s-baseline-agents-back)"
  printf 'vxlan-dropped\t%s\n' "$(run_id_of k8s-baseline-vxlan-dropped)"
  printf 'vxlan-restored\t%s\n' "$(run_id_of k8s-baseline-vxlan-restored)"
} > "$out/runs.tsv"
printf '\nsteps: %s\nruns:  %s\n' "$out/steps.tsv" "$out/runs.tsv"
awk -F'\t' 'NR > 1 && (($2 == "0" && $3 != 0) || ($2 == "nonzero" && $3 == 0)) { bad++ } END { print (bad ? bad : 0) " unexpected exit codes"; exit (bad ? 1 : 0) }' "$out/steps.tsv"
