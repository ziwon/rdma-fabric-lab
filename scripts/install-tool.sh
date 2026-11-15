#!/usr/bin/env bash
# Install one binary from a release archive into .tools/bin after checking its sha256.
# usage: install-tool.sh <name> <url> <sha256:hex> <path of the binary inside the archive>
set -euo pipefail

name=${1:?name} url=${2:?url} sum=${3:?sha256} member=${4:?member}
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
bin="$repo/.tools/bin"
mkdir -p "$bin"
work=$(mktemp -d "$repo/.tools/.download.XXXXXX")
trap 'rm -rf -- "$work"' EXIT

curl -fsSL --retry 3 -o "$work/archive" "$url"
printf '%s  %s\n' "${sum#sha256:}" "$work/archive" | sha256sum --check --quiet -
tar -xzf "$work/archive" -C "$work" "$member"
install -m 0755 "$work/$member" "$bin/$name.new"
mv -f "$bin/$name.new" "$bin/$name"
echo "installed $name from $url"
