#!/usr/bin/env bash
# Run the Track B scenario commands on this host with every command they could start replaced by a stub.
set -euo pipefail
repo=$1 work=$2
rm -rf "$work"
mkdir -p "$work/bin"
calls=$work/calls
for name in docker systemd-detect-virt ibsim opensm ibnetdiscover iblinkinfo ibswitches ibhosts ibroute ibtracert sminfo smpquery saquery ibstat; do
  printf '#!/bin/sh\necho "%s $*" >> %s\n' "$name" "$calls" > "$work/bin/$name"
  chmod 755 "$work/bin/$name"
done
export PATH=$work/bin:$PATH
echo "\$ uname -r; systemd-detect-virt --vm (real binary); ls -l /etc/rfl/track-b-guest"
uname -r
/usr/bin/systemd-detect-virt --vm || true
ls -l /etc/rfl/track-b-guest 2>&1 || true
echo "\$ command -v docker ibsim opensm ibnetdiscover (stubs first on PATH)"
command -v docker ibsim opensm ibnetdiscover | sed "s#$work#<stubs>#"
key=20261116T000000Z-b-01
image=sha256:cb3e3550f4f60de5414abe55dbe81e45fd68106ef5ecc4cfa4d701b6083ee279
for args in "start $key $image hca1 -R minhop" "build images/ibsim rfl-ibsim:m4 APT_SNAPSHOT=20261115T000000Z" \
            "exec $key tool ibnetdiscover" "read $key opensm.log" "inspect $key" "top $key" "stop $key" "ls"; do
  echo "\$ tracks/b-ibsim/scripts/guest.sh $args < tracks/b-ibsim/topologies/b-01.net"
  rc=0
  # shellcheck disable=SC2086
  "$repo/tracks/b-ibsim/scripts/guest.sh" $args < "$repo/tracks/b-ibsim/topologies/b-01.net" 2>&1 || rc=$?
  echo "rc=$rc"
done
for args in "fabric /dev/null" "tool ibnetdiscover" "console Unlink"; do
  echo "\$ SIM_HOST=hca1 images/ibsim/rfl-ibsim $args"
  rc=0
  # shellcheck disable=SC2086
  SIM_HOST=hca1 "$repo/images/ibsim/rfl-ibsim" $args 2>&1 || rc=$?
  echo "rc=$rc"
done
echo "\$ cat <stubs>/calls   # every call that reached a stub"
if [[ -s $calls ]]; then cat "$calls"; else echo "(no call reached docker, systemd-detect-virt or a simulator command)"; fi
echo "\$ ps -eo pid=,comm=,args= | awk '\$2 ~ /^(ibsim|opensm|ibnetdiscover|iblinkinfo|ibswitches|ibhosts|ibroute|sminfo|smpquery)\$/'"
ps -eo pid=,comm=,args= | awk '$2 ~ /^(ibsim|opensm|ibnetdiscover|iblinkinfo|ibswitches|ibhosts|ibroute|sminfo|smpquery)$/' || true
echo "(end of process list)"
rm -rf "$work"
