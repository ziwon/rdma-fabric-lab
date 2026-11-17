#!/usr/bin/env bash
# shellcheck disable=SC2016 # single-quoted commands expand inside the guest
# Answers the question the Multus secondary network builds on, by observation inside the lab workers:
# do frames from extra MAC addresses on the lab NIC cross the harness link between worker1 and worker2?
#
#   probe-extra-mac.sh macvlan|ipvlan
#
# On each worker it puts one child of the given type on lab_rdma_nic into a throwaway network namespace,
# with one of the last two addresses of lab_address_plan.multus_pool, and pings the peer's child a
# finite number of times while tcpdump on each lab NIC records the link-layer source of every frame.
# It also records the GIDs of the RXE device before, during and after, since RXE lists addresses of
# devices stacked on its NIC. The children and namespaces are removed on exit. The log lands in
# .lab/probe-extra-mac/<UTC time>-<type>/probe.log.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
kind=${1:-}
case $kind in
  macvlan) mode='mode bridge' ;;
  ipvlan) mode='mode l2' ;;
  *) echo "usage: probe-extra-mac.sh macvlan|ipvlan" >&2; exit 2 ;;
esac
out=$repo/.lab/probe-extra-mac/$(date -u +%Y%m%dT%H%M%SZ)-$kind
mkdir -p "$out"
log=$out/probe.log
lab() { "$here/lab.sh" "$@"; }
ns=rfl-xmac

step() {
  local guest=$1 rc=0
  shift
  printf '\n### %s $ %s\n' "$guest" "$*" >> "$log"
  lab ssh "$guest" "$@" >> "$log" 2>&1 < /dev/null || rc=$?
  printf '### exit %s\n' "$rc" >> "$log"
  return "$rc"
}

# worker -> "nic ip pool_cidr_prefix" from the same inventories the playbooks read.
read -r -a inventories <<< "$("$here/lab.sh" inventories)"
facts=$("$repo/.venv/bin/ansible-inventory" -i "$repo/${inventories[0]}" -i "${inventories[1]}" --list | "$repo/.venv/bin/python" -c '
import ipaddress, json, sys
listing = json.load(sys.stdin)
hostvars = listing["_meta"]["hostvars"]
workers = listing["workers"]["hosts"]
plan = hostvars[workers[0]]["lab_address_plan"]
prefix = ipaddress.ip_network(plan["rdma"]).prefixlen
last = ipaddress.ip_address(plan["multus_pool"].split("-")[1])
for offset, name in enumerate(workers):
    h = hostvars[name]
    print(name, h["lab_rdma_nic"], h["lab_rxe_device"], f"{last - offset}/{prefix}", last - offset)
')
declare -A nic dev child addr
while read -r name n d c a; do nic[$name]=$n dev[$name]=$d child[$name]=$c addr[$name]=$a; done <<< "$facts"
w1=worker1 w2=worker2
printf 'type %s %s\n%s\n' "$kind" "$mode" "$facts" > "$log"

gids() { step "$1" "for g in /sys/class/infiniband/${dev[$1]}/ports/1/gids/*; do v=\$(cat \$g 2>/dev/null) || continue; case \$v in 0000:0000:0000:0000:0000:0000:0000:0000) ;; *) echo \"\${g##*/} \$v \$(cat /sys/class/infiniband/${dev[$1]}/ports/1/gid_attrs/ndevs/\${g##*/} 2>/dev/null)\";; esac; done; rdma link show" || true; }

cleanup() {
  for w in $w1 $w2; do step "$w" "sudo pkill -INT -f 'tcpdump -n -e -i ${nic[$w]} -w /tmp/$ns' ; sudo ip netns del $ns 2>/dev/null; ip -br link | grep -c $ns-c || true" || true; done
}
trap cleanup EXIT

for w in $w1 $w2; do gids "$w"; done
for w in $w1 $w2; do
  step "$w" "sudo ip netns add $ns && sudo ip link add $ns-c link ${nic[$w]} type $kind $mode && sudo ip link set $ns-c netns $ns \
    && sudo ip -n $ns addr add ${child[$w]} dev $ns-c && sudo ip -n $ns link set $ns-c up && sudo ip -n $ns link set lo up \
    && sudo ip -n $ns -d link show $ns-c && ip -d link show ${nic[$w]}"
  step "$w" "sudo setsid timeout 60 tcpdump -n -e -i ${nic[$w]} -w /tmp/$ns.pcap 'icmp or arp' > /dev/null 2>&1 < /dev/null & sleep 2; pgrep -a tcpdump"
done
for w in $w1 $w2; do gids "$w"; done
rc=0
step $w1 "sudo ip netns exec $ns ping -c 5 -W 2 ${addr[$w2]}" || rc=$?
step $w2 "sudo ip netns exec $ns ping -c 5 -W 2 ${addr[$w1]}" || rc=$?
for w in $w1 $w2; do
  step "$w" "sudo ip -n $ns neigh show; sleep 1; sudo pkill -INT -f 'tcpdump -n -e -i ${nic[$w]} -w /tmp/$ns'; sleep 1; \
    sudo tcpdump -n -e -r /tmp/$ns.pcap 2>/dev/null | head -n 40; sudo rm -f /tmp/$ns.pcap" || true
done
cleanup
trap - EXIT
for w in $w1 $w2; do gids "$w"; done
printf '\nverdict: cross-node ping between %s children %s\n' "$kind" "$([[ $rc == 0 ]] && echo passed || echo "failed (exit $rc)")" | tee -a "$log"
echo "log: $log"
exit "$rc"
