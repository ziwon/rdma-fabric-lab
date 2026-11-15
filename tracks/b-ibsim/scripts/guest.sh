#!/usr/bin/env bash
# Track B commands that run inside the Track B guest, as root through sudo. Each one refuses to run
# anywhere else before it touches Docker, because the host kernel crashes under ibsim.
#
#   guest.sh build <context> <tag> KEY=VALUE...    build the image, its ID on stdout, the build log on stderr
#   guest.sh start <key> <image-id> <sim-host> [opensm option...] < netfile
#                                                   start the fabric container of one run, print its docker run
#   guest.sh exec <key> <rfl-ibsim argument...>     rfl-ibsim tool or console inside that container
#   guest.sh read <key> ibsim.out|ibsim.err|opensm.out|opensm.log
#   guest.sh inspect <key>                          docker inspect of the container
#   guest.sh top <key>                              UID, PID and command of every process in the container
#   guest.sh stop <key>                             remove the container and the run's files, if any
#   guest.sh ls                                     every Track B container, running or not
#
# <key> is <run-id>-<scenario>, for example 20261115T140000Z-b-01.
set -euo pipefail

marker=/etc/rfl/track-b-guest
root=/var/tmp/rfl-track-b
user=10001:10001

die() { printf 'guest.sh: %s\n' "$*" >&2; exit 2; }

require_guest() {
  [[ -r $marker ]] || die "refusing to run: $marker is missing, so this machine is not the Track B guest." \
    "ibsim, OpenSM and the diagnostic tools run only inside the guest of tests/integration/qemu-lab/profiles/ibsim.tsv." \
    "On the host, run make track-b."
  local virt
  virt=$(systemd-detect-virt --vm 2> /dev/null || true)
  [[ -n $virt && $virt != none ]] || die "refusing to run: systemd-detect-virt finds no virtual machine here."
}

key_of() {
  [[ $1 =~ ^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]*$ ]] || die "$1 is not <run-id>-<scenario>"
  printf '%s\n' "$1"
}

name_of() { printf 'rfl-ibsim-%s\n' "$(key_of "$1")"; }

run_dir() {
  local dir
  dir=$(realpath -m "$root/runs/$(key_of "$1")")
  [[ $dir == "$root/runs/"* ]] || die "refusing run directory $dir"
  printf '%s\n' "$dir"
}

build() {
  [[ $# -ge 2 ]] || die "usage: guest.sh build <context> <tag> KEY=VALUE..."
  local context=$1 tag=$2 kv args=()
  shift 2
  for kv in "$@"; do
    [[ $kv =~ ^[A-Z][A-Z_]*=[^[:space:]]+$ ]] || die "build argument $kv is not KEY=VALUE"
    args+=(--build-arg "$kv")
  done
  docker build --progress=plain --provenance=false --sbom=false --build-context guest-ca=/etc/ssl/certs \
    --tag "$tag" "${args[@]}" "$context" >&2
  # The image ID that docker image inspect and every container of the image report, the manifest digest.
  docker image inspect --format '{{.Id}}' "$tag"
}

start() {
  [[ $# -ge 3 ]] || die "usage: guest.sh start <key> <image-id> <sim-host> [opensm option...] < netfile"
  local key=$1 image=$2 sim_host=$3 name dir
  shift 3
  [[ $image =~ ^sha256:[0-9a-f]{64}$ ]] || die "$image is not an image ID"
  name=$(name_of "$key")
  dir=$(run_dir "$key")
  ! docker container inspect "$name" > /dev/null 2>&1 || die "container $name already exists. Run guest.sh stop $key first."
  mkdir -p "$dir"
  cat > "$dir/topology.net"
  local args=(
    run --detach --name "$name" --label rfl.track=b-ibsim --label "rfl.run=$key"
    --network none --cap-drop ALL --security-opt no-new-privileges --read-only
    --tmpfs "/run/rfl:rw,nosuid,nodev,noexec,size=64m,uid=10001,gid=10001,mode=0700"
    --user "$user" --pids-limit 256 --memory 1g --cpus 2
    --env "SIM_HOST=$sim_host"
    --volume "$dir/topology.net:/topology.net:ro"
    --volume "$marker:/run/rfl-guest/track-b-guest:ro"
    "$image" fabric /topology.net "$@"
  )
  printf '%q ' docker "${args[@]}" > "$dir/docker-run.txt"
  printf '\n' >> "$dir/docker-run.txt"
  docker "${args[@]}" > "$dir/container.id"
  cat "$dir/docker-run.txt"
}

case ${1:-} in
  build | start | exec | read | inspect | top | stop | ls) require_guest ;;
  *) die "usage: guest.sh build|start|exec|read|inspect|top|stop|ls ..." ;;
esac

case $1 in
  build) shift; build "$@" ;;
  start) shift; start "$@" ;;
  exec) [[ $# -ge 3 ]] || die "usage: guest.sh exec <key> <rfl-ibsim argument...>"; docker exec "$(name_of "$2")" rfl-ibsim "${@:3}" ;;
  read)
    [[ $# -eq 3 && $3 =~ ^(ibsim\.out|ibsim\.err|opensm\.out|opensm\.log)$ ]] || die "usage: guest.sh read <key> ibsim.out|ibsim.err|opensm.out|opensm.log"
    docker exec "$(name_of "$2")" cat "/run/rfl/$3"
    ;;
  inspect) [[ $# -eq 2 ]] || die "usage: guest.sh inspect <key>"; docker container inspect "$(name_of "$2")" ;;
  top) [[ $# -eq 2 ]] || die "usage: guest.sh top <key>"; docker top "$(name_of "$2")" -eo uid,pid,comm ;;
  stop)
    [[ $# -eq 2 ]] || die "usage: guest.sh stop <key>"
    docker rm --force "$(name_of "$2")" > /dev/null 2>&1 || true
    rm -rf -- "$(run_dir "$2")"
    ;;
  ls) docker ps --all --filter label=rfl.track=b-ibsim --format '{{.Names}} {{.Status}} {{.Image}}' ;;
esac
