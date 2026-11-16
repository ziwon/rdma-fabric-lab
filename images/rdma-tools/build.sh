#!/usr/bin/env bash
# Builds the RDMA test image inside a harness guest, as root through sudo, into an OCI archive. It refuses
# to run anywhere but a virtual machine, because the host runs no new container.
#
#   build.sh <context> <image name> <archive> KEY=VALUE...
#
# Every build starts without cache, and SOURCE_DATE_EPOCH with rewrite-timestamp clamps every time in
# the image, so the same inputs give the same bytes. The digest of the archive's OCI index, which
# ctr import --digests names the image by and the pods reference, goes to stdout and the build log to stderr.
set -euo pipefail

die() { printf 'build.sh: %s\n' "$*" >&2; exit 2; }

[[ $# -ge 3 ]] || die "usage: build.sh <context> <image name> <archive> KEY=VALUE..."
virt=$(systemd-detect-virt --vm 2> /dev/null || true)
[[ -n $virt && $virt != none ]] || die "refusing to run: systemd-detect-virt finds no virtual machine here."
context=$1 name=$2 archive=$3
shift 3
args=()
for kv in "$@"; do
  [[ $kv =~ ^[A-Z][A-Z_]*=[^[:space:]]+$ ]] || die "build argument $kv is not KEY=VALUE"
  args+=(--build-arg "$kv")
done
mkdir -p "$(dirname "$archive")"
rm -f "$archive.part"
docker build --no-cache --progress=plain --provenance=false --sbom=false --build-context guest-ca=/etc/ssl/certs \
  "${args[@]}" --output "type=oci,name=$name,rewrite-timestamp=true,dest=$archive.part" "$context" >&2
mv "$archive.part" "$archive"
tar -xOf "$archive" index.json | python3 -c 'import hashlib, json, sys
index = sys.stdin.buffer.read()
manifests = json.loads(index)["manifests"]
assert len(manifests) == 1, f"want one manifest in the archive, got {len(manifests)}"
print("sha256:" + hashlib.sha256(index).hexdigest())'
