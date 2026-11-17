#!/usr/bin/env bash
# Every required experiment again from wiped guests, one harness profile at a time: acceptance-m3.sh (M1 to M3),
# make collect and make track-a on the same lab, acceptance-ha.sh, make track-b and track-b-breaks, make track-c
# CLEAN=1 and track-c-breaks, and acceptance-cel1.sh last because it boots 13 guests. Each profile is stopped
# before the next boots, and every boot waits for a step that fails while any QEMU process runs. It ends with
# make validate and make report STRICT=1. Steps land in .lab/acceptance-all/<UTC time>/NN-<step>.log with
# steps.tsv, which holds each step's start, end and seconds.
#
#   LAB_ROOT=<dir with room> acceptance-all.sh     each profile's guests live in LAB_ROOT/<profile>
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
: "${LAB_ROOT:?set LAB_ROOT to a directory with room for 13 guest disks, such as /data/rfl-lab/m7}"
root=$(realpath -m "$LAB_ROOT")
out=$repo/.lab/acceptance-all/$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$out" "$root"
cd "$repo"
export PATH=$repo/.venv/bin:$PATH
n=0
printf 'step\texpect\texit\tstart\tend\tseconds\tlog\n' > "$out/steps.tsv"

step() {
  local name=$1 expect=$2 rc=0 log start end
  shift 2
  n=$((n + 1))
  log=$(printf '%s/%02d-%s.log' "$out" "$n" "$name")
  printf '$ %s\n' "$*" > "$log"
  start=$(date -u +%s)
  "$@" >> "$log" 2>&1 || rc=$?
  end=$(date -u +%s)
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$expect" "$rc" "$(date -u -d "@$start" +%FT%TZ)" \
    "$(date -u -d "@$end" +%FT%TZ)" "$((end - start))" "${log#"$repo"/}" >> "$out/steps.tsv"
  local verdict=ok
  if [[ $expect == 0 && $rc != 0 ]] || [[ $expect == nonzero && $rc == 0 ]]; then verdict=UNEXPECTED; fi
  printf '%02d %-32s expect %-7s exit %-3s %6ss %s\n' "$n" "$name" "$expect" "$rc" "$((end - start))" "$verdict"
}

in_profile() { # <profile> <command...>: the command with that profile's LAB_DIR
  local profile=$1
  shift
  env LAB_PROFILE="$profile" LAB_DIR="$root/$profile" "$@"
}

no_guests() {
  local running
  running=$(pgrep -af '^qemu-system' | sed -E 's/ -drive .*//' || true)
  [[ -z $running ]] || { printf 'QEMU processes still run:\n%s\n' "$running"; return 1; }
  echo "no QEMU process runs"
}

only_lab_guests() {
  local others
  pgrep -af '^qemu-system-x86_64 ' | sed -E 's/ -drive .*//' || true
  others=$(pgrep -af '^qemu-system-x86_64 ' | grep -vE -- '-name (cp1|worker1|worker2),process=rfl-lab-' || true)
  [[ -z $others ]] || { echo "other guests run: $others"; return 1; }
}

host() { uptime; free -m; df -B1 --output=target,avail /cache / /data; du -sb "$root"; }

step host-before 0 host
step no-guests-before-lab 0 no_guests
step acceptance-m3 0 in_profile lab "$here/acceptance-m3.sh"
step lab-only-before-collect 0 only_lab_guests
step collect 0 in_profile lab make collect
step lab-only-before-track-a 0 only_lab_guests
step track-a 0 in_profile lab make track-a
step lab-down 0 in_profile lab make lab-down
step builder-down 0 env LAB_PROFILE=ibsim LAB_DIR="$root/lab/builder" make lab-down
step no-guests-before-ha 0 no_guests
step acceptance-ha 0 in_profile ha "$here/acceptance-ha.sh"
step ha-down 0 in_profile ha make lab-down
step no-guests-before-track-b 0 no_guests
step ibsim-wipe 0 in_profile ibsim make lab-down lab-wipe
step track-b 0 in_profile ibsim make track-b
step track-b-breaks 0 in_profile ibsim make track-b-breaks
step ibsim-down 0 in_profile ibsim make lab-down
step no-guests-before-track-c 0 no_guests
step ns3-wipe 0 in_profile ns3 make lab-down lab-wipe
step track-c 0 in_profile ns3 make track-c CLEAN=1
step track-c-breaks 0 in_profile ns3 make track-c-breaks
step ns3-down 0 in_profile ns3 make lab-down
step no-guests-before-cel1 0 no_guests
step acceptance-cel1 0 in_profile cel1 "$here/acceptance-cel1.sh"
step cel1-down 0 in_profile cel1 make lab-down
step no-guests-at-end 0 no_guests
step validate 0 make validate
step report 0 make report STRICT=1
step host-end 0 host

total=$(awk -F'\t' 'NR > 1 { s += $6 } END { print s + 0 }' "$out/steps.tsv")
printf '\nsteps: %s\ntotal: %s s from %s to %s\n' "$out/steps.tsv" "$total" \
  "$(awk -F'\t' 'NR == 2 { print $4 }' "$out/steps.tsv")" "$(tail -n1 "$out/steps.tsv" | cut -f5)"
awk -F'\t' 'NR > 1 && (($2 == "0" && $3 != 0) || ($2 == "nonzero" && $3 == 0)) { bad++ } END { print (bad ? bad : 0) " unexpected exit codes"; exit (bad ? 1 : 0) }' "$out/steps.tsv"
