#!/usr/bin/env bash
# QEMU/KVM verification harness: unprivileged guests of one profile for the lab inventories.
#
#   lab.sh up              boot every guest of the profile and write its override inventory
#   lab.sh down            power off the guests and switches this harness started
#   lab.sh status          show which guests and switches run
#   lab.sh wipe            delete guest disks, seeds and sockets so the next up starts from nothing
#   lab.sh ssh <guest> [command...]
#   lab.sh stop <guest>    power off one guest and leave the rest running
#   lab.sh kill <guest>    cut the power of one guest: SIGKILL to its QEMU, no shutdown inside
#   lab.sh start <guest>   boot one guest of a profile that is up
#   lab.sh inventories     print the -i arguments of the profile
#   lab.sh api-url         print the forwarded API address of the first control plane
#
# LAB_PROFILE picks profiles/<name>.tsv (default lab). guests.py documents the profile format.
# Every NIC is QEMU user networking or an AF_UNIX datagram socket, so no frame leaves the host.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
profile=${LAB_PROFILE:-lab}
profile_file=$here/profiles/$profile.tsv
root=$repo/.lab
lab=${LAB_DIR:-$root$([[ $profile == lab ]] || printf '/%s' "$profile")}
py=$repo/.venv/bin/python
lock=$repo/versions.lock.yaml
key=$root/ssh/id_ed25519
known_hosts=$lab/ssh/known_hosts
guest_user=ubuntu
export ANSIBLE_CONFIG=$repo/ansible/ansible.cfg

die() { printf 'lab.sh: %s\n' "$*" >&2; exit 1; }
say() { printf 'lab.sh: %s\n' "$*" >&2; }

[[ -f $profile_file ]] || die "no profile $profile_file"

guests() { "$py" -I "$here/guests.py" "$1" "$profile_file" "${@:2}"; }

# name ssh_port vcpus mem_mib disk_gib
guest_rows() { guests rows; }

guest_row() {
  local row
  row=$(guest_rows | awk -v n="$1" '$1 == n') || true
  [[ -n $row ]] || die "unknown guest $1 in profile $profile. Known: $(guest_rows | cut -f1 | tr '\n' ' ')"
  printf '%s\n' "$row"
}

inventory_path() { printf '%s/%s\n' "$repo" "$(guests inventory-path)"; }

listing() {
  mkdir -p "$lab"
  "$repo/.venv/bin/ansible-inventory" -i "$(inventory_path)" --list > "$lab/.listing.json.tmp"
  mv "$lab/.listing.json.tmp" "$lab/.listing.json"
}

lock_field() {
  "$py" -c 'import sys, yaml
print(yaml.safe_load(open(sys.argv[1]))["components"][sys.argv[2]][sys.argv[3]])' "$lock" "$1" "$2"
}

image_path() { printf '%s/images/sha256-%s.img\n' "$root" "$(lock_field lab.guest_image value | sed 's/^sha256://')"; }

ensure_image() {
  local url sum file
  url=$(lock_field lab.guest_image source)
  sum=$(lock_field lab.guest_image value | sed 's/^sha256://')
  file=$(image_path)
  if [[ ! -f $file ]]; then
    mkdir -p "$root/images"
    say "downloading $url"
    curl -fsSL --retry 3 -o "$file.part" "$url"
    mv "$file.part" "$file"
  fi
  printf '%s  %s\n' "$sum" "$file" | sha256sum --check --quiet - \
    || die "$file does not hash to lab.guest_image in versions.lock.yaml. Refusing to boot it."
  chmod a-w "$file"
}

ensure_keypair() { [[ -f $1 ]] || ssh-keygen -q -t ed25519 -N '' -C "$2" -f "$1"; }

# Prints the pid only when the pidfile names a live process whose command line starts with
# exe and carries that same pidfile.
owned_pid() {
  local pidfile=$1 exe=$2 pid cmdline
  [[ -f $pidfile ]] || return 1
  read -r pid < "$pidfile" || return 1
  [[ $pid =~ ^[0-9]+$ && -r /proc/$pid/cmdline ]] || return 1
  cmdline=$(tr '\0' '\n' < "/proc/$pid/cmdline")
  if grep -qE -- "$exe" <<< "$(head -n2 <<< "$cmdline")" && grep -qxF -- "$pidfile" <<< "$cmdline"; then
    printf '%s\n' "$pid"
    return 0
  fi
  return 1
}

qemu_pid() { owned_pid "$1" 'qemu-system-x86_64$'; }
switch_pid() { owned_pid "$1" 'python[0-9.]*$'; }

guest_ssh() {
  local port=$1
  shift
  ssh -i "$key" -p "$port" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=5 \
    -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known_hosts" "$guest_user@127.0.0.1" "$@"
}

write_seed() {
  local name=$1 dir=$lab/guests/$1
  ensure_keypair "$dir/ssh_host_ed25519_key" "root@$name"
  {
    printf '#cloud-config\n'
    printf 'hostname: %s\n' "$name"
    printf 'manage_etc_hosts: localhost\n'
    printf 'users:\n  - name: %s\n' "$guest_user"
    printf '    sudo: "ALL=(ALL) NOPASSWD:ALL"\n    shell: /bin/bash\n    lock_passwd: true\n'
    printf '    ssh_authorized_keys:\n      - %s\n' "$(cat "$key.pub")"
    printf 'ssh_pwauth: false\n'
    printf 'ssh_genkeytypes: [ed25519]\n'
    printf 'ssh_keys:\n  ed25519_private: |\n'
    sed 's/^/    /' "$dir/ssh_host_ed25519_key"
    printf '  ed25519_public: %s\n' "$(cat "$dir/ssh_host_ed25519_key.pub")"
    # The kernel stays the one the image shipped, so it matches lab.guest_kernel in the lock.
    printf 'package_update: false\npackage_upgrade: false\n'
  } > "$dir/user-data"
  printf 'instance-id: rfl-%s\nlocal-hostname: %s\n' "$name" "$name" > "$dir/meta-data"
  guests network-config --guest "$name" --listing "$lab/.listing.json" > "$dir/network-config"
  cloud-localds -N "$dir/network-config" "$dir/seed.iso" "$dir/user-data" "$dir/meta-data"
}

start_switch() {
  local net=$1 dir=$lab/net/$1 port_args=() member
  shift
  if switch_pid "$dir/switch.pid" > /dev/null; then
    say "switch $net already runs"
    return 0
  fi
  mkdir -p "$dir"
  rm -f "$dir/switch.pid"
  for member in "$@"; do port_args+=(--port "$member"); done
  setsid "$py" -I "$here/switch.py" --dir "$dir" --pidfile "$dir/switch.pid" "${port_args[@]}" \
    < /dev/null >> "$dir/switch.log" 2>&1 &
  local deadline=$((SECONDS + 10))
  until switch_pid "$dir/switch.pid" > /dev/null; do
    ((SECONDS < deadline)) || die "switch $net did not start. See $dir/switch.log"
    sleep 0.2
  done
  say "switch $net started, pid $(cat "$dir/switch.pid"), ports $*"
}

start_guest() {
  local name=$1 port=$2 vcpus=$3 mem=$4 disk_gib=$5 dir=$lab/guests/$1 nic_args
  if qemu_pid "$dir/qemu.pid" > /dev/null; then
    say "$name already runs"
    return 0
  fi
  mkdir -p "$dir" "$lab/net"
  [[ -f $dir/disk.qcow2 ]] || qemu-img create -q -f qcow2 -F qcow2 -b "$(image_path)" "$dir/disk.qcow2" "${disk_gib}G"
  write_seed "$name"
  mapfile -t nic_args < <(guests qemu-nics --guest "$name" --lab-dir "$lab")
  rm -f "$lab/net/cable-$name-"*.sock "$lab/net/"*"/$name.nic"
  qemu-system-x86_64 \
    -name "$name,process=rfl-lab-$name" \
    -machine q35,accel=kvm -cpu host -smp "$vcpus" -m "$mem" \
    -drive "file=$dir/disk.qcow2,if=virtio,format=qcow2" \
    -drive "file=$dir/seed.iso,if=virtio,format=raw,readonly=on" \
    "${nic_args[@]}" \
    -display none \
    -chardev "file,id=serial0,path=$dir/serial.log,append=on" -serial chardev:serial0 \
    -monitor "unix:$dir/monitor.sock,server=on,wait=off" \
    -pidfile "$dir/qemu.pid" -daemonize
  say "$name started, pid $(cat "$dir/qemu.pid"), ssh 127.0.0.1:$port"
}

known_host_line() {
  local name=$1 port=$2
  printf '[127.0.0.1]:%s %s\n' "$port" "$(cut -d' ' -f1,2 "$lab/guests/$name/ssh_host_ed25519_key.pub")"
}

wait_ready() {
  local name=$1 port=$2 deadline=$((SECONDS + 600))
  until guest_ssh "$port" true < /dev/null 2> /dev/null; do
    ((SECONDS < deadline)) || die "$name: no SSH on 127.0.0.1:$port after 600 s. See $lab/guests/$name/serial.log"
    sleep 3
  done
  guest_ssh "$port" timeout 600 cloud-init status --wait < /dev/null > /dev/null \
    || die "$name: cloud-init did not finish cleanly. See $lab/guests/$name/serial.log"
}

write_inventory() {
  local out=$lab/inventory.yml name port
  {
    printf '{'
    local sep=''
    while IFS=$'\t' read -r name port _; do
      printf '%s"%s": ' "$sep" "$name"
      guest_ssh "$port" ip -j link show < /dev/null \
        | "$py" -c 'import json, sys; print(json.dumps({l["address"]: l["ifname"] for l in json.load(sys.stdin) if "address" in l}))'
      sep=','
    done < <(guest_rows)
    printf '}\n'
  } > "$lab/.discovered.json"
  guests override --listing "$lab/.listing.json" --discovered "$lab/.discovered.json" \
    --key "$key" --known-hosts "$known_hosts" > "$out.tmp"
  mv "$out.tmp" "$out"
  say "wrote $out"
}

boot() {
  local name port rest
  while IFS=$'\t' read -r name port rest; do
    [[ $# -eq 0 || $name == "$1" ]] || continue
    # shellcheck disable=SC2086 # rest is the tab-separated remainder of one guest row
    start_guest "$name" "$port" $rest
  done < <(guest_rows)
}

cmd_up() {
  [[ $lab != *,* ]] || die "$lab contains a comma, which QEMU option parsing cannot take"
  [[ -r /dev/kvm && -w /dev/kvm ]] || die "/dev/kvm is not readable and writable for $(id -un)"
  mkdir -p "$root/ssh" "$lab/ssh"
  ensure_image
  ensure_keypair "$key" rfl-lab
  listing
  local net members name port
  while read -r net members; do
    # shellcheck disable=SC2086 # members is a space-separated guest list
    start_switch "$net" $members
  done < <(guests switches)
  boot
  : > "$known_hosts.tmp"
  while IFS=$'\t' read -r name port _; do known_host_line "$name" "$port" >> "$known_hosts.tmp"; done < <(guest_rows)
  mv "$known_hosts.tmp" "$known_hosts"
  while IFS=$'\t' read -r name port _; do
    wait_ready "$name" "$port"
    say "$name ready"
  done < <(guest_rows)
  write_inventory
}

monitor_command() {
  "$py" -c 'import socket, sys, time
s = socket.socket(socket.AF_UNIX)
s.connect(sys.argv[1])
s.settimeout(2)
s.recv(4096)
s.sendall(sys.argv[2].encode() + b"\n")
time.sleep(0.5)
s.close()' "$1" "$2"
}

stop_guest() {
  local name=$1 dir=$lab/guests/$1 pid deadline
  if ! pid=$(qemu_pid "$dir/qemu.pid"); then
    say "$name does not run under this harness"
    return 0
  fi
  monitor_command "$dir/monitor.sock" system_powerdown || true
  deadline=$((SECONDS + 120))
  while [[ -d /proc/$pid ]] && ((SECONDS < deadline)); do sleep 2; done
  if pid=$(qemu_pid "$dir/qemu.pid"); then
    say "$name ignored ACPI power-off for 120 s, sending SIGTERM to $pid"
    kill -TERM "$pid"
  fi
  rm -f "$dir/qemu.pid"
  say "$name stopped"
}

kill_guest() {
  local name=$1 dir=$lab/guests/$1 pid deadline
  if ! pid=$(qemu_pid "$dir/qemu.pid"); then
    say "$name does not run under this harness"
    return 0
  fi
  kill -KILL "$pid"
  deadline=$((SECONDS + 30))
  while [[ -d /proc/$pid ]] && ((SECONDS < deadline)); do sleep 0.2; done
  [[ ! -d /proc/$pid ]] || die "$name: QEMU $pid survived SIGKILL for 30 s"
  rm -f "$dir/qemu.pid"
  say "$name powered off hard, SIGKILL to QEMU $pid"
}

stop_switch() {
  local net=$1 dir=$lab/net/$1 pid
  if pid=$(switch_pid "$dir/switch.pid"); then
    kill -TERM "$pid"
    say "switch $net stopped"
  fi
  rm -f "$dir/switch.pid"
}

cmd_down() {
  local name net
  while IFS=$'\t' read -r name _; do stop_guest "$name"; done < <(guest_rows)
  while read -r net _; do stop_switch "$net"; done < <(guests switches)
}

cmd_status() {
  local name port pid net
  while IFS=$'\t' read -r name port _; do
    if pid=$(qemu_pid "$lab/guests/$name/qemu.pid"); then
      printf '%-8s running  pid %-8s ssh 127.0.0.1:%s\n' "$name" "$pid" "$port"
    else
      printf '%-8s stopped\n' "$name"
    fi
  done < <(guest_rows)
  while read -r net _; do
    if pid=$(switch_pid "$lab/net/$net/switch.pid"); then
      printf 'switch %-6s running  pid %s\n' "$net" "$pid"
    else
      printf 'switch %-6s stopped\n' "$net"
    fi
  done < <(guests switches)
}

cmd_wipe() {
  local name target net
  while IFS=$'\t' read -r name _; do
    ! qemu_pid "$lab/guests/$name/qemu.pid" > /dev/null || die "$name still runs. Run lab.sh down first."
  done < <(guest_rows)
  while read -r net _; do
    ! switch_pid "$lab/net/$net/switch.pid" > /dev/null || die "switch $net still runs. Run lab.sh down first."
  done < <(guests switches)
  while IFS=$'\t' read -r name _; do
    target=$(realpath -m "$lab/guests/$name")
    [[ $target == "$(realpath -m "$lab")/guests/$name" ]] || die "refusing to delete $target"
    rm -rf -- "$target"
    rm -f -- "$lab/net/$name.sock" "$lab/net/cable-$name-"*.sock "$lab/net/"*"/$name."{nic,port}
    say "wiped $target"
  done < <(guest_rows)
  rm -f -- "$lab/inventory.yml" "$known_hosts" "$lab/.listing.json" "$lab/.discovered.json"
}

cmd_ssh() {
  [[ $# -ge 1 ]] || die "usage: lab.sh ssh <guest> [command...]"
  local port
  port=$(guest_row "$1" | cut -f2)
  shift
  guest_ssh "$port" "$@"
}

cmd_start() {
  [[ $# -eq 1 ]] || die "usage: lab.sh start <guest>"
  guest_row "$1" > /dev/null
  [[ -f $lab/.listing.json ]] || die "profile $profile is not up. Run lab.sh up first."
  boot "$1"
  wait_ready "$1" "$(guest_row "$1" | cut -f2)"
  say "$1 ready"
}

case ${1:-} in
  up) cmd_up ;;
  down) cmd_down ;;
  status) cmd_status ;;
  wipe) cmd_wipe ;;
  ssh) shift; cmd_ssh "$@" ;;
  stop) [[ $# -eq 2 ]] || die "usage: lab.sh stop <guest>"; guest_row "$2" > /dev/null; stop_guest "$2" ;;
  kill) [[ $# -eq 2 ]] || die "usage: lab.sh kill <guest>"; guest_row "$2" > /dev/null; kill_guest "$2" ;;
  start) shift; cmd_start "$@" ;;
  inventories) printf '%s %s\n' "$(guests inventory-path)" "${lab#"$repo"/}/inventory.yml" ;;
  api-url) [[ -f $lab/.listing.json ]] || listing; guests api-url --listing "$lab/.listing.json" ;;
  *) die "usage: lab.sh up|down|status|wipe|ssh <guest> [command...]|stop <guest>|kill <guest>|start <guest>|inventories|api-url" ;;
esac
