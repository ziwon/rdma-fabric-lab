#!/usr/bin/env bash
# The cel1 simulation from wiped guests: the fleet targets refuse without CONFIRM=cel1 and the
# simulation inventory and contact nothing, build the cluster, change nothing on a second run, and
# cel1-sim-baseline passes, fails on a deliberate break and passes after the repair. cel1-rail-networks
# (ADR 0007) runs on the same cluster, and its breaks are tests/integration/qemu-lab/breaks-cel1-rails.sh.
# Logs land in .lab/acceptance-cel1/<UTC time>/NN-<step>.log with steps.tsv and runs.tsv.
# LAB_DIR moves the guests' disks, as for lab.sh. The guests are stopped at the end.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
out=$repo/.lab/acceptance-cel1/$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$out"
cd "$repo"
export LAB_PROFILE=cel1
export LAB_DIR=${LAB_DIR:-$repo/.lab/cel1}
connect=$LAB_DIR/inventory.yml
kubeconfig=${XDG_STATE_HOME:-$HOME/.local/state}/rdma-fabric-lab/cel1/kubeconfig
fleet=(CONFIRM=cel1 "FLEET_CONNECT=$connect")
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
kubectl_cp() { "$here/lab.sh" ssh cel1-cp-02 "sudo kubectl --kubeconfig /etc/kubernetes/admin.conf $*" < /dev/null; }

unchanged() {
  awk '/^PLAY RECAP/ { recap = 1; hosts = 0; bad = 0; next }
       recap && /changed=/ { hosts++; if ($0 !~ /changed=0 /) bad++ }
       END { printf "%d hosts, %d with changes\n", hosts, bad; exit (hosts == 0 || bad) }' "$(last_log "$1")"
}

# Every changed task of the log of step $1 is the node label convergence, on hosts that name the
# GPU Operator labels it removed. Prints the changed tasks.
only_label_changes() {
  awk '/^TASK \[/ { task = $0; sub(/^TASK \[/, "", task); sub(/\] .*$/, "", task) }
       /^changed: / { print task " on " $2; if (task != "kubeadm : Set the node labels the inventory names") bad++ }
       END { exit (bad > 0) }' "$(last_log "$1")"
}

# make <target> under strace. It must exit 2 and make no connect, sendto or sendmsg call to an IPv4 or IPv6 address.
refused() {
  local trace=$out/strace-$n.txt rc=0 inet
  strace -f -qq -e trace=connect,sendto,sendmsg -o "$trace" make --no-print-directory "$@" || rc=$?
  inet=$(grep -cE 'AF_INET6?' "$trace" || true)
  printf 'exit %s, %s calls to an IPv4 or IPv6 address in %s\n' "$rc" "$inet" "${trace#"$repo"/}"
  [[ $rc == 2 && $inet == 0 ]]
}

harness() {
  ss -xlp | grep -F "$LAB_DIR/net/" | awk '{print $1}' | sort | uniq -c
  local pid
  for pid in $(pgrep -f "^qemu-system-x86_64 -name cel1-") $(pgrep -f "switch.py --dir $LAB_DIR/net/"); do
    printf '%s apparmor=%s %s\n' "$(tr '\0' ' ' < "/proc/$pid/cmdline" | cut -c1-48)" "$(cat "/proc/$pid/attr/current")" \
      "$(grep CapEff "/proc/$pid/status" | tr -s '\t' ' ')"
  done | sort | uniq -c
  ! ss -xlp | grep -F "$LAB_DIR/net/" | grep -qv '^u_dgr'
}

host() { uptime; free -m; df -B1 --output=target,avail /cache / /data; if [[ -d $LAB_DIR ]]; then du -sb "$LAB_DIR"; else echo "$LAB_DIR does not exist yet"; fi; }

opt_out_labels() {
  kubectl_cp get nodes -L nvidia.com/gpu.deploy.driver,nvidia.com/gpu.deploy.container-toolkit,nvidia.com/gpu.present
}

dgx_opted_out() { opt_out_labels | awk '/^cel1-dgx-/ { n++; if ($6 == "false" && $7 == "false") ok++ } END { print ok + 0 " of " n " DGX Nodes carry both opt-outs"; exit (ok != 8) }'; }

releases=(cilium:kube-system multus:kube-system gpu-operator:gpu-operator network-operator:nvidia-network-operator
  rail-networks:nvidia-network-operator)
release_names=$(printf '%s\n' "${releases[@]%%:*}" | paste -sd'|')

releases_unchanged() {
  grep -E "^($release_names): " "$(last_log "$1")"
  [[ $(grep -cE "^($release_names): unchanged" "$(last_log "$1")") == "${#releases[@]}" ]]
}

one_revision() {
  local release
  for release in "${releases[@]}"; do
    .tools/bin/helm history "${release%%:*}" -n "${release#*:}" --kubeconfig "$kubeconfig" -o json \
      | .venv/bin/python -c 'import json, sys; h = json.load(sys.stdin); print(sys.argv[1], [r["revision"] for r in h]); sys.exit([r["revision"] for r in h] != [1])' "${release%%:*}"
  done
}

invented_connect() {
  printf '%s\n' 'all:' '  hosts:' \
    '    cel1-cp-01: {ansible_host: 10.140.0.11, ansible_port: 22, rfl_kubeconfig_server: "https://10.140.0.10:6443"}' \
    > "$out/invented-connect.yml"
  printf '%s\n' 'apiVersion: v1' 'kind: Config' 'clusters:' '  - name: kubernetes' '    cluster: {server: "https://10.140.0.10:6443"}' \
    > "$out/invented-kubeconfig.yml"
  cat "$out/invented-connect.yml" "$out/invented-kubeconfig.yml"
}

stop_rsyslog_on_dgx06() { "$here/lab.sh" ssh cel1-dgx-06 'sudo systemctl stop rsyslog.service rsyslog.socket syslog.socket 2>/dev/null; systemctl is-active rsyslog.service || true' < /dev/null; }

step lab-down 0 make lab-down
step lab-wipe 0 make lab-wipe
step host-before 0 host
step sim-profile-check 0 make fleet-sim-profile-check
step lab-up 0 make lab-up
step harness 0 harness
step host-up 0 host
step invented-addresses 0 invented_connect
step refuse-k8s-without-confirm 0 refused fleet-k8s
step refuse-k8s-without-connect 0 refused fleet-k8s CONFIRM=cel1
step refuse-k8s-invented-addresses 0 refused fleet-k8s CONFIRM=cel1 "FLEET_CONNECT=$out/invented-connect.yml"
step refuse-rsyslog-without-confirm 0 refused fleet-rsyslog
step refuse-rsyslog-without-connect 0 refused fleet-rsyslog CONFIRM=cel1
step refuse-rsyslog-invented-addresses 0 refused fleet-rsyslog CONFIRM=cel1 "FLEET_CONNECT=$out/invented-connect.yml"
step refuse-stand-ins-without-connect 0 refused fleet-sim-stand-ins CONFIRM=cel1
step refuse-cni-migrate-without-confirm 0 refused fleet-cni-migrate
step refuse-rails-test-without-connect 0 refused fleet-rails-test
step stand-ins 0 make fleet-sim-stand-ins "${fleet[@]}"
step fleet-k8s 0 make fleet-k8s "${fleet[@]}"
# The NFD label stand-in of the rail hosts needs their Nodes, so it runs again once they exist.
step stand-ins-after-k8s 0 make fleet-sim-stand-ins "${fleet[@]}"
step kubeconfig-mode 0 stat -c '%a %n' "$kubeconfig"
step opt-outs-before-operator 0 dgx_opted_out
step fleet-k8s-again 0 make fleet-k8s "${fleet[@]}"
step fleet-k8s-again-unchanged 0 unchanged fleet-k8s-again
step refuse-addons-without-confirm 0 refused fleet-addons
step refuse-addons-without-connect 0 refused fleet-addons CONFIRM=cel1
step refuse-addons-invented-kubeconfig 0 refused fleet-addons "${fleet[@]}" "CEL1_KUBECONFIG=$out/invented-kubeconfig.yml"
step addons-template 0 make addons-template ENV=cel1
step fleet-addons 0 make fleet-addons "${fleet[@]}"
step fleet-rsyslog 0 make fleet-rsyslog "${fleet[@]}"
step opt-outs-after-operator 0 opt_out_labels
step cel1-sim-baseline 0 make fleet-sim-test "FLEET_CONNECT=$connect"
step fleet-cni-migrate-on-new-layout 0 make fleet-cni-migrate "${fleet[@]}"
step fleet-cni-migrate-on-new-layout-unchanged 0 unchanged fleet-cni-migrate-on-new-layout
step cel1-rail-networks 0 make fleet-rails-test "FLEET_CONNECT=$connect"
step fleet-k8s-after-operator 0 make fleet-k8s "${fleet[@]}"
step fleet-k8s-after-operator-changes 0 only_label_changes fleet-k8s-after-operator
step fleet-k8s-converged 0 make fleet-k8s "${fleet[@]}"
step fleet-k8s-converged-unchanged 0 unchanged fleet-k8s-converged
step fleet-addons-again 0 make fleet-addons "${fleet[@]}"
step fleet-addons-again-unchanged 0 releases_unchanged fleet-addons-again
step helm-one-revision 0 one_revision
step fleet-rsyslog-again 0 make fleet-rsyslog "${fleet[@]}"
step fleet-rsyslog-again-unchanged 0 unchanged fleet-rsyslog-again
step break-stop-rsyslog-on-dgx-06 0 stop_rsyslog_on_dgx06
step cel1-sim-baseline-rsyslog-stopped nonzero env RFL_BREAK=rsyslog-stopped \
  RFL_BREAK_WHAT='rsyslog stopped on cel1-dgx-06 just before the run' make fleet-sim-test "FLEET_CONNECT=$connect"
step repair-fleet-rsyslog 0 make fleet-rsyslog "${fleet[@]}"
step cel1-sim-baseline-repaired 0 make fleet-sim-test "FLEET_CONNECT=$connect"
step inventory-check-cel1 0 make inventory-check INVENTORY=cel1
step validate 0 make validate
step host-end 0 host
step lab-down-end 0 make lab-down

{
  printf 'cel1-sim-baseline\t%s\n' "$(run_id_of cel1-sim-baseline)"
  printf 'rsyslog-stopped\t%s\n' "$(run_id_of cel1-sim-baseline-rsyslog-stopped)"
  printf 'repaired\t%s\n' "$(run_id_of cel1-sim-baseline-repaired)"
  printf 'cel1-rail-networks\t%s\n' "$(run_id_of cel1-rail-networks)"
} > "$out/runs.tsv"
printf '\nsteps: %s\nruns:  %s\n' "$out/steps.tsv" "$out/runs.tsv"
awk -F'\t' 'NR > 1 && (($2 == "0" && $3 != 0) || ($2 == "nonzero" && $3 == 0)) { bad++ } END { print (bad ? bad : 0) " unexpected exit codes"; exit (bad ? 1 : 0) }' "$out/steps.tsv"
