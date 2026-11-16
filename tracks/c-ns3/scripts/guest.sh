#!/usr/bin/env bash
# Track C commands that run inside the Track C guest, as root through sudo. Each one refuses to run
# anywhere else before it touches Docker, because the simulator and its build never run on the host.
#
#   guest.sh build <context> <driver-dir> <tag> KEY=VALUE...   build the image, its ID on stdout, the build log on stderr
#   guest.sh clean <tag> <base-image>                          delete the tag, the base image and all build cache
#   guest.sh save <tag> <image-id>                             docker save of the tag on stdout, when it is that image
#   guest.sh run <key> <slot> <image-id> [unlocked] < inputs.tar
#                                                              run in/steps.tsv in one container, print its docker run
#   guest.sh fetch <key> <slot>                                tar of the slot's output directory on stdout
#   guest.sh inspect <key> <slot>                              docker inspect of the slot's container
#   guest.sh stop <key>                                        remove the run's containers and files, if any
#   guest.sh ls                                                every Track C container, running or not
#
# <key> is <run-id>-<scenario>, for example 20261116T140000Z-c-02-single-flow. <slot> names one container
# run of that scenario, for example run1. "unlocked" leaves out --read-only, for a deliberate break only.
set -euo pipefail

marker=/etc/rfl/track-c-guest
root=/var/tmp/rfl-track-c
user=10001:10001

die() { printf 'guest.sh: %s\n' "$*" >&2; exit 2; }

require_guest() {
  [[ -r $marker ]] || die "refusing to run: $marker is missing, so this machine is not the Track C guest." \
    "The ns-3 image is built and run only inside the guest of tests/integration/qemu-lab/profiles/ns3.tsv." \
    "On the host, run make track-c."
  local virt
  virt=$(systemd-detect-virt --vm 2> /dev/null || true)
  [[ -n $virt && $virt != none ]] || die "refusing to run: systemd-detect-virt finds no virtual machine here."
}

key_of() {
  [[ $1 =~ ^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]*$ ]] || die "$1 is not <run-id>-<scenario>"
  printf '%s\n' "$1"
}

slot_of() {
  [[ $1 =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "$1 is not a slot name"
  printf '%s\n' "$1"
}

name_of() { printf 'rfl-ns3-%s-%s\n' "$(key_of "$1")" "$(slot_of "$2")"; }

run_dir() {
  local dir
  dir=$(realpath -m "$root/runs/$(key_of "$1")")
  [[ $dir == "$root/runs/"* ]] || die "refusing run directory $dir"
  printf '%s\n' "$dir"
}

image_id() { [[ $1 =~ ^sha256:[0-9a-f]{64}$ ]] || die "$1 is not an image ID"; }

build() {
  [[ $# -ge 3 ]] || die "usage: guest.sh build <context> <driver-dir> <tag> KEY=VALUE..."
  local context=$1 driver=$2 tag=$3 kv epoch="" args=()
  shift 3
  for kv in "$@"; do
    [[ $kv =~ ^[A-Z][A-Z0-9_]*=[^[:space:]]+$ ]] || die "build argument $kv is not KEY=VALUE"
    [[ $kv == SOURCE_DATE_EPOCH=* ]] && epoch=${kv#*=}
    args+=(--build-arg "$kv")
  done
  [[ $epoch =~ ^[0-9]+$ ]] || die "build needs SOURCE_DATE_EPOCH=<seconds>, so the image carries no build time"
  docker buildx build --progress=plain --provenance=false --sbom=false \
    --build-context guest-ca=/etc/ssl/certs --build-context "driver=$driver" \
    --output "type=image,name=$tag,rewrite-timestamp=true,unpack=false" "${args[@]}" "$context" >&2
  # rewrite-timestamp conflicts with the unpack of type=docker. Docker unpacks the image when a container starts.
  # The image ID that docker image inspect and every container of the image report, the manifest digest.
  docker image inspect --format '{{.Id}}' "$tag"
}

clean() {
  [[ $# -eq 2 ]] || die "usage: guest.sh clean <tag> <base-image>"
  docker image rm --force "$1" > /dev/null 2>&1 || true
  docker image rm --force "$2" > /dev/null 2>&1 || true
  docker builder prune --all --force >&2
  ! docker image inspect "$1" > /dev/null 2>&1 || die "$1 is still present"
  printf 'removed %s and %s and every build cache entry\n' "$1" "$2"
}

run() {
  [[ $# -eq 3 || ($# -eq 4 && $4 == unlocked) ]] || die "usage: guest.sh run <key> <slot> <image-id> [unlocked] < inputs.tar"
  local key=$1 slot=$2 image=$3 name dir rc=0
  image_id "$image"
  name=$(name_of "$key" "$slot")
  dir=$(run_dir "$key")/$slot
  ! docker container inspect "$name" > /dev/null 2>&1 || die "container $name already exists. Run guest.sh stop $key first."
  rm -rf -- "$dir"
  mkdir -p "$dir/in"
  tar -x -C "$dir/in" --no-same-owner --no-same-permissions
  chmod -R a+rX,go-w "$dir/in"
  install -d -o 10001 -g 10001 -m 0700 "$dir/out"
  local lock=(--read-only)
  [[ ${4:-} == unlocked ]] && lock=()
  local args=(
    run --name "$name" --label rfl.track=c-ns3 --label "rfl.run=$key"
    --network none --cap-drop ALL --security-opt no-new-privileges "${lock[@]}"
    --tmpfs "/tmp:rw,nosuid,nodev,noexec,size=64m"
    --user "$user" --pids-limit 256 --memory 8g --cpus 8
    --volume "$dir/in:/run/rfl/in:ro"
    --volume "$dir/out:/run/rfl/out:rw"
    --volume "$marker:/run/rfl-guest/track-c-guest:ro"
    "$image" steps
  )
  printf '%q ' docker "${args[@]}" > "$dir/docker-run.txt"
  printf '\n' >> "$dir/docker-run.txt"
  cat "$dir/docker-run.txt"
  timeout --kill-after 10 3600 docker "${args[@]}" > "$dir/container.log" 2>&1 || rc=$?
  printf '%s\n' "$rc" > "$dir/rc"
  exit "$rc"
}

case ${1:-} in
  build | clean | save | run | fetch | inspect | stop | ls) require_guest ;;
  *) die "usage: guest.sh build|clean|save|run|fetch|inspect|stop|ls ..." ;;
esac

case $1 in
  build) shift; build "$@" ;;
  clean) shift; clean "$@" ;;
  save)
    [[ $# -eq 3 ]] || die "usage: guest.sh save <tag> <image-id>"
    image_id "$3"
    [[ $(docker image inspect --format '{{.Id}}' "$2") == "$3" ]] || die "$2 is not $3"
    docker save "$2"
    ;;
  run) shift; run "$@" ;;
  fetch)
    [[ $# -eq 3 ]] || die "usage: guest.sh fetch <key> <slot>"
    tar -C "$(run_dir "$2")/$(slot_of "$3")" -cf - out docker-run.txt container.log rc
    ;;
  inspect) [[ $# -eq 3 ]] || die "usage: guest.sh inspect <key> <slot>"; docker container inspect "$(name_of "$2" "$3")" ;;
  stop)
    [[ $# -eq 2 ]] || die "usage: guest.sh stop <key>"
    key_of "$2" > /dev/null
    docker ps --all --quiet --filter "label=rfl.run=$2" --filter label=rfl.track=c-ns3 | xargs -r docker rm --force > /dev/null
    rm -rf -- "$(run_dir "$2")"
    ;;
  ls) docker ps --all --filter label=rfl.track=c-ns3 --format '{{.Names}} {{.Status}} {{.Image}}' ;;
esac
