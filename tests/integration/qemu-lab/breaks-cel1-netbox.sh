#!/usr/bin/env bash
# shellcheck disable=SC2016 # single-quoted commands expand inside the guests
# The deliberate breaks of cel1-netbox on a running NetBox guest that make netbox-guest prepared. Each break runs
# the scenario, which must fail with the named checks failed, then repairs the break and runs the scenario again, which
# must pass. Steps land in .lab/breaks-cel1-netbox/<UTC time>/ with steps.tsv, and each break with its two run ids in
# pairs.tsv. LAB_DIR moves the guests' files, as for lab.sh.
#
#   breaks-cel1-netbox.sh [break name...]     only the named breaks, every one without a name
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
out=$repo/.lab/breaks-cel1-netbox/$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$out/configs"
cd "$repo"
export LAB_PROFILE=netbox PATH=$repo/.venv/bin:$PATH
export LAB_DIR=${LAB_DIR:-$repo/.lab/netbox}
n=0
printf 'step\texpect\texit\tlog\n' > "$out/steps.tsv"
printf 'break\tbroken_run\tbroken_status\tfailed_checks\trepaired_run\trepaired_status\n' > "$out/pairs.tsv"

lab() { "$here/lab.sh" "$@"; }
netbox_test() { make --no-print-directory netbox-test "$@"; }
value() { python3 -c 'import sys, yaml; print(yaml.safe_load(open(sys.argv[1]))[sys.argv[2]])' "$1" "$2"; }

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
  printf '%02d %-44s expect %-7s exit %-3s %s\n' "$n" "$name" "$expect" "$rc" "$verdict"
}

last_log() { local logs=("$out"/*-"$1".log); printf '%s\n' "${logs[-1]}"; }
manifest_of() { grep -oE 'results/[0-9]{8}T[0-9]{6}Z/cel1/cel1-netbox' "$(last_log "$1")" | head -n1; }

# The run of step $1 ended failed, and each of the remaining arguments is one of its failed checks.
failed_with() {
  local dir
  dir=$(manifest_of "$1")
  shift
  python3 - "$repo/$dir/manifest.json" "$@" <<'EOF'
import json, sys
manifest = json.load(open(sys.argv[1]))
failed = [c["id"] for c in manifest["checks"] if c["status"] == "failed"]
missing = [c for c in sys.argv[2:] if c not in failed]
print(f"{manifest['run_id']} {manifest['status']}; failed {failed}; want failed {sys.argv[2:]}; missing {missing}")
sys.exit(manifest["status"] != "failed" or bool(missing))
EOF
}

record_pair() {
  local name=$1 broken=$2 repaired=$3 bdir rdir
  bdir=$(manifest_of "$broken")
  rdir=$(manifest_of "$repaired")
  python3 - "$name" "$repo/$bdir/manifest.json" "$repo/$rdir/manifest.json" >> "$out/pairs.tsv" <<'EOF'
import json, sys
b, r = (json.load(open(p)) for p in sys.argv[2:])
failed = ",".join(c["id"] for c in b["checks"] if c["status"] == "failed")
print("\t".join([sys.argv[1], b["run_id"], b["status"], failed, r["run_id"], r["status"]]))
EOF
}

broken_run() {
  local name=$1 what=$2
  shift 2
  RFL_BREAK=$name RFL_BREAK_WHAT=$what "$@"
}

# breaks <name> <checks that must fail> -- <break command> -- <repair command> [-- <broken run options>]
selected=("$@")
breaks() {
  local name=$1 checks=$2
  shift 2
  if [[ ${#selected[@]} -gt 0 && " ${selected[*]} " != *" $name "* ]]; then return 0; fi
  local -a brk=() fix=() opts=()
  [[ $1 == -- ]] && shift
  while [[ $# -gt 0 && $1 != -- ]]; do brk+=("$1"); shift; done
  [[ $# -gt 0 ]] && shift
  while [[ $# -gt 0 && $1 != -- ]]; do fix+=("$1"); shift; done
  [[ $# -gt 0 ]] && shift
  opts=("$@")
  if [[ ${#brk[@]} -gt 0 ]]; then step "break-$name" 0 "${brk[@]}"; fi
  step "run-$name-broken" nonzero broken_run "$name" "breaks-cel1-netbox.sh break $name: ${brk[*]:-the options ${opts[*]}}" netbox_test "${opts[@]}"
  # shellcheck disable=SC2086 # checks is a space-separated list of check ids
  step "failed-$name" 0 failed_with "run-$name-broken" $checks
  if [[ ${#fix[@]} -gt 0 ]]; then step "repair-$name" 0 "${fix[@]}"; fi
  step "run-$name-repaired" 0 netbox_test
  record_pair "$name" "run-$name-broken" "run-$name-repaired"
}

config=tracks/cel1/config/cel1-netbox.yaml
config_with() { # <out name> <python statement on c>
  python3 - "$config" "$out/configs/$1" "$2" <<'EOF2'
import sys, yaml
c = yaml.safe_load(open(sys.argv[1]))
exec(sys.argv[3])
open(sys.argv[2], "w").write(yaml.safe_dump(c, sort_keys=False))
EOF2
  printf 'CONFIG=%s\n' "$out/configs/$1"
}

lock_backup=$out/versions.lock.yaml.orig
cp versions.lock.yaml "$lock_backup"
restore_lock() { cp "$lock_backup" versions.lock.yaml; }
trap restore_lock EXIT
other_netbox() { sed -i 's|ref: docker.io/netboxcommunity/netbox:v4.7.5-5.1.2|ref: docker.io/netboxcommunity/netbox:v4.7.4-5.1.2|' versions.lock.yaml && grep -n 'netboxcommunity/netbox:' versions.lock.yaml; }

# A second writer inside the guest: it sets the description of one managed address every half second, so a sync is never
# the last word. It edits another address than the run's own drift, so the drift comparison also finds it.
writer_address=10.142.6.25/24
second_writer() {
  lab ssh netbox1 "sudo systemd-run --unit rfl-break-writer -p Restart=always python3 -c '
import json, time, urllib.parse, urllib.request
s = json.load(open(\"/etc/rfl/netbox/secrets.json\"))
h = {\"Authorization\": \"Bearer nbt_\" + s[\"api_key\"] + \".\" + s[\"api_token\"], \"Content-Type\": \"application/json\"}
base = \"http://127.0.0.1:8080/api/ipam/ip-addresses/\"
q = urllib.request.Request(base + \"?\" + urllib.parse.urlencode({\"address\": \"$writer_address\"}), headers=h)
ip = json.load(urllib.request.urlopen(q))[\"results\"][0][\"id\"]
n = 0
while True:
    n += 1
    body = json.dumps({\"description\": \"second writer \" + str(n)}).encode()
    urllib.request.urlopen(urllib.request.Request(base + str(ip) + \"/\", data=body, method=\"PATCH\", headers=h))
    time.sleep(0.5)
'" < /dev/null
}
stop_writer() { lab ssh netbox1 'sudo systemctl stop rfl-break-writer; sudo systemctl reset-failed rfl-break-writer 2>/dev/null; true' < /dev/null && make --no-print-directory netbox-sync; }
# The address a break run left behind, removed as a person would.
remove_hand_made() {
  PYTHONPATH=scripts python - "$(value "$config" hand_made_address)" <<'EOF2'
import sys
from pathlib import Path
from netbox import client
inv = [Path(p) for p in __import__("shlex").split(__import__("os").environ["NETBOX_INV"])]
c = client.Client(client.DEFAULT_URL, client.token(client.secrets(inv)))
rows = [{"id": o["id"]} for o in c.list("ipam/ip-addresses", {"address": sys.argv[1]})]
if rows:
    c.delete("ipam/ip-addresses", rows)
print(f"removed {len(rows)} objects at {sys.argv[1]}")
EOF2
}
read -r -a pair <<< "$(lab inventories)"
export NETBOX_INV="${pair[0]} ${pair[1]}"

breaks lock-names-another-netbox "netbox-is-the-locked-version" \
  -- other_netbox \
  -- restore_lock
breaks sync-skips-cables "sync-makes-netbox-hold-the-model netbox-equals-the-model" \
  -- -- -- "$(config_with skip-cables.yaml 'c["sync_skips"] = ["cable"]')"
breaks second-writer "second-sync-changes-nothing drift-is-found-exactly" \
  -- second_writer \
  -- stop_writer
breaks prune-off "sync-undoes-the-drift" \
  -- -- make --no-print-directory netbox-sync -- "$(config_with prune-off.yaml 'c["prune"] = False')"
breaks hand-made-carries-the-tag "hand-made-objects-stay" \
  -- -- -- "$(config_with tagged.yaml 'c["hand_made_tagged"] = True')"
breaks hand-made-kept "run-objects-removed" \
  -- -- remove_hand_made -- "$(config_with keep.yaml 'c["keep_hand_made"] = True')"

step validate 0 make validate
printf '\nsteps: %s\npairs: %s\n' "$out/steps.tsv" "$out/pairs.tsv"
awk -F'\t' 'NR > 1 && (($2 == "0" && $3 != 0) || ($2 == "nonzero" && $3 == 0)) { bad++ } END { print (bad ? bad : 0) " unexpected exit codes"; exit (bad ? 1 : 0) }' "$out/steps.tsv"
