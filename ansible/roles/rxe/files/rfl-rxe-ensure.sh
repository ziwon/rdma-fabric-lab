#!/usr/bin/env bash
# Adds the lab Soft-RoCE device once its NIC is up and holds its address.
# rfl-rxe.service runs this at every boot and the rxe role starts that unit, so the
# first run and every reboot share one code path. It never deletes a device.
set -euo pipefail
: "${RXE_DEVICE:?}" "${RXE_NETDEV:?}" "${RXE_ADDRESS:?}" "${RXE_WAIT_SECONDS:?}"

deadline=$((SECONDS + RXE_WAIT_SECONDS))
until [[ $(cat "/sys/class/net/$RXE_NETDEV/operstate" 2> /dev/null) == up ]] \
  && ip -4 -o addr show dev "$RXE_NETDEV" | grep -qF " inet $RXE_ADDRESS "; do
  if ((SECONDS >= deadline)); then
    echo "rfl-rxe: $RXE_NETDEV is not up with $RXE_ADDRESS after ${RXE_WAIT_SECONDS} s, $RXE_DEVICE not added" >&2
    exit 1
  fi
  sleep 1
done

modprobe rdma_rxe

# One "device netdev" pair per RDMA link, from lines like "link rxe0/1 state ACTIVE ... netdev enp0s4".
links=$(rdma link show | awk '{ split($2, dev, "/"); for (i = 3; i < NF; i++) if ($i == "netdev") print dev[1], $(i + 1) }')
bound_to=$(awk -v d="$RXE_DEVICE" '$1 == d { print $2 }' <<< "$links")
other=$(awk -v d="$RXE_DEVICE" -v n="$RXE_NETDEV" '$2 == n && $1 != d { print $1 }' <<< "$links")

if [[ $bound_to == "$RXE_NETDEV" ]]; then
  echo "rfl-rxe: $RXE_DEVICE already on $RXE_NETDEV"
  exit 0
fi
if [[ -n $bound_to ]]; then
  echo "rfl-rxe: $RXE_DEVICE exists on $bound_to, not on $RXE_NETDEV. Check what uses it and remove it by hand." >&2
  exit 1
fi
if [[ -n $other ]]; then
  echo "rfl-rxe: $RXE_NETDEV already carries RDMA device $other. Refusing to add $RXE_DEVICE as a second one." >&2
  exit 1
fi
rdma link add "$RXE_DEVICE" type rxe netdev "$RXE_NETDEV"
echo "rfl-rxe: added $RXE_DEVICE on $RXE_NETDEV"
