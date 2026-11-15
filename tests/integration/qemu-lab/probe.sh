#!/usr/bin/env bash
# shellcheck disable=SC2016 # single-quoted commands expand inside the guest
# Answers the three questions M1 builds on, by observation on freshly booted guests:
#   1. Does the guest kernel ship rdma_rxe, or which package provides it?
#   2. Does the lab NIC carry frames between the guests?
#   3. Can rdma link add bind rxe0 to that NIC, and does rping complete across it?
# It changes runtime state and installs packages, so run it on throwaway guests and
# `lab.sh wipe` afterwards. The log lands in .lab/probe/<UTC time>/probe.log.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
out=$repo/.lab/probe/$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$out"
log=$out/probe.log
lab() { "$here/lab.sh" "$@"; }

step() {
  local guest=$1
  shift
  printf '\n### %s $ %s\n' "$guest" "$*" >> "$log"
  local rc=0
  lab ssh "$guest" "$@" >> "$log" 2>&1 < /dev/null || rc=$?
  printf '### exit %s\n' "$rc" >> "$log"
  return 0
}

nic() { awk -v h="$1" -v k="$2" '$1 == h":" {f=1} f && $1 == k":" {print $2; exit}' "$repo/.lab/inventory.yml"; }

declare -A ip=([worker1]=10.77.0.12 [worker2]=10.77.0.13)
pkgs="rdma-core ibverbs-utils rdmacm-utils perftest tcpdump"

for g in worker1 worker2; do
  step "$g" uname -r
  step "$g" 'modinfo -n rdma_rxe'
  step "$g" 'find /lib/modules/$(uname -r) -name "rdma_rxe*"'
  step "$g" 'sudo apt-get update -q >/dev/null && apt-cache policy linux-modules-extra-$(uname -r) '"$pkgs"
  step "$g" 'sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q linux-modules-extra-$(uname -r) '"$pkgs"' >/dev/null && echo installed'
  step "$g" 'modinfo -n rdma_rxe && dpkg -S "$(modinfo -n rdma_rxe)"'
  step "$g" "sudo ip link set dev $(nic "$g" lab_rdma_nic) up && sudo ip addr replace ${ip[$g]}/24 dev $(nic "$g" lab_rdma_nic) && ip -br addr show dev $(nic "$g" lab_rdma_nic)"
done

printf '\n### host $ ss -xa | grep .lab/net\n' >> "$log"
ss -xap 2>/dev/null | grep -F "$repo/.lab/net" >> "$log" || true

step worker2 "ping -c 3 -W 2 -I $(nic worker2 lab_rdma_nic) ${ip[worker1]}"
step worker2 "ip neigh show dev $(nic worker2 lab_rdma_nic)"
step worker2 "ip route get ${ip[worker1]}"

for g in worker1 worker2; do
  step "$g" "sudo modprobe rdma_rxe && sudo rdma link add rxe0 type rxe netdev $(nic "$g" lab_rdma_nic) && rdma link show && ibv_devinfo -d rxe0"
done

step worker1 'rping 2>&1 | head -30 || true'
step worker1 "nohup timeout 60 rping -s -a ${ip[worker1]} -C 5 -S 64 -V -v > /tmp/rping-server.log 2>&1 & sleep 2; rdma resource show cm_id"
step worker2 "timeout 30 rping -c -a ${ip[worker1]} -C 5 -S 64 -V -v"
step worker1 'sleep 1; cat /tmp/rping-server.log; pgrep -a rping || echo no rping left'
printf '\nlog: %s\n' "$log"
grep -c '^### exit 0' "$log" | sed 's/^/steps with exit 0: /'
