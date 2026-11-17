# ADR 0002: Execution environment per track

> Status: accepted, 2026-11-15. Section 5, the image rules, accepted 2026-11-16 after two pinned digests failed to reproduce. Section 6 records how the rules of section 3 are enforced.

## 1. Decision

| Target | Execution environment | Reason |
|---|---|---|
| Track A (RXE, Kubernetes) | QEMU guest. Pods are containers of the Kubernetes inside the guest | RXE is a kernel device. The two workers are truly separate only when each guest has its own kernel and RDMA stack. Running the kubeadm role unchanged is what proves the cel1 code |
| cel1 simulation | QEMU guest | Same as Track A |
| Track B (ibsim, OpenSM) | Version-pinned OCI image. It runs in a container runtime inside a dedicated QEMU guest | This keeps Track B as one reproducible container that runs ibsim, OpenSM and the diagnostic tools, and isolates it from the host kernel |
| Track C (ns-3) | Same as Track B | Same reason. Both the build and the runs happen inside the guest |
| Offline checks (lint, rendering, kubeconform) | Host process | No simulator that uses sockets runs |

## 2. Reasons

- Containers share the host kernel. A host kernel bug freezes the whole host even when it fires inside a container.
- This happened on 2026-11-15. On the Ubuntu `7.0.0-42` and `7.0.0-43` kernels, a process that AppArmor confines hits a NULL dereference when it calls `write()` on a connected AF_UNIX datagram socket. ibsim and libumad2sim use this socket. Docker confines every container with the `docker-default` profile. So `ibnetdiscover` and `opensm` inside host containers froze the host three times. The upstream fix is "apparmor: fix NULL ctx->peer derefs in unix socket ctx updates".
- Inside a guest, the same bug freezes only that guest.
- The host RDMA namespace mode is `shared`. Two containers cannot separate the RDMA devices of two workers.

## 3. Rules

- No simulator container starts on the host. This rule is reviewed again only after a kernel with the fix is verified in a VM and installed on the host.
- The make targets of Track B and Track C run only inside a guest. A direct call on the host is refused.
- Containers run without privileged mode, host network or added capabilities. The same holds inside a guest.

## 4. Follow-up: NetBox integration

This is not implemented now. This section only fixes where it attaches.

- The source of truth stays `sites/cel1.yaml`. NetBox starts as the side that receives that model, shows it and compares against it.
- A sync script moves the site model into NetBox site, device, interface, cable, prefix and IP address objects. The InfiniBand rail wiring also becomes cables.
- A reconciliation check reports the differences between NetBox and the site model. It fails when any difference exists.
- Whether NetBox becomes the source of truth is a separate decision after the reconciliation check is stable.
- NetBox itself follows the rules of this ADR and runs as a container inside a guest.

## 5. Every image rebuilds to its pinned digest

This holds for the three images the lab builds: the Track B image `rfl-ibsim`, the Track C image `rfl-ns3` and the RDMA test image `rfl-rdma-tools` of the hostNetwork RDMA pods.

**Decision.**

1. A guest builds each image. The Track B guest builds `rfl-ibsim` and, as the builder of `make rdma-tools-image`, `rfl-rdma-tools`. The Track C guest builds `rfl-ns3`. Nothing builds on the host.
2. The base image is `ubuntu:24.04` by digest, never by tag. Packages come only from the archive snapshot of the lock at their locked versions, and `apt-get update --error-on=any` fails the build when the snapshot is unreachable. The build mounts the guest's CA bundle for that step alone, and apt still checks every index against the Ubuntu keyring.
3. The build deletes the files that carry build-time state: the apt lists, the apt, dpkg and alternatives logs, the ldconfig aux cache and the debconf and dpkg `-old` backups, in Track B and Track C also `/var/cache/apt/*.bin`, and in Track C every `__pycache__`. No build runs `useradd`, which writes the day of the build into `/etc/shadow`. The Track B image appends its account to `/etc/passwd` and `/etc/group`, and the other two run as a numeric user.
4. BuildKit exports with `rewrite-timestamp=true` and `SOURCE_DATE_EPOCH`: the time of `ibsim.apt_snapshot` for Track B, the commit time of `ns3.simai` for Track C. Track B and Track C export with `type=image,unpack=false`, because BuildKit 0.30.1 refuses `rewrite-timestamp` with the unpack that `type=docker` implies. The RDMA test image goes to an OCI archive.
5. The copy of the build context in the guest carries the modes Git keeps, 0755 for directories and executables and 0644 for the rest, whatever umask made the checkout.
6. Sources come at pinned commits. `images/ns3/fetch-sources` never runs `git submodule update --remote` and refuses a submodule commit other than the gitlink SimAI records. Only build outputs reach the final stage of the Track C image.
7. A build with no cache must give the pinned digest. `make track-c-image CLEAN=1` removes the tag, the base image and every BuildKit cache entry first. `make rdma-tools-image` always builds with `--no-cache`. A changed digest therefore means a changed input, and the lock moves only after that input is known.
8. The pods get the RDMA test image without a registry. `make rdma-tools-load` imports the archive with `ctr import --digests`, and the lock pins the sha256 of the archive's `index.json`, the name CRI resolves.

**Reasons.** A digest pin proves only what a rebuild can repeat. Two pins did not repeat. The Track B ID `sha256:cb3e3550…` came from a cached build, and a wiped guest built `sha256:0b462da8…`. The old Dockerfile kept the apt and dpkg logs, the ldconfig cache and the backups, wrote the build day into `/etc/shadow`, and exported without `rewrite-timestamp`. No old image was kept, so which of these changed the ID was not isolated. The Track C image built from a wiped guest gave `sha256:2f14f43e…`, because `/usr/local/bin/rfl-ns3` had mode 0775 from a checkout made with umask 002. Its content was identical.

**Evidence.**

- Track B: two builds of the fixed Dockerfile, each from a wiped guest, gave `sha256:e4374db2a49fae94b28717bc905d9e06ef5d02dd0a8ccfd3a3e087163053d2de` (`.lab/evidence/m7/ibsim-digest/track-b-image-1.out`, `track-b-image-2.out`). `make track-b` run `20261116T194712Z` passed on it.
- Track C: `make track-c-image CLEAN=1` gave `sha256:da7a3245b1b91dd472d1ddb9497b3bd1abb3efdbaee38c45fe790dff565a61c6` in three separate guests: the build of record (`.lab/evidence/m6/image-build-clean-1.out`), its rerun in a new guest, and the full reproduction that ran `c-01` to `c-06` as run `20261116T200409Z`. The one-bit layer difference of the umask defect is in `.lab/evidence/m7/ns3-digest/layer-diff.txt`. `tests/unit/test_guest_copy_modes.py` fails without the mode rule.
- RDMA test image: three builds without cache in one builder and a fourth in a wiped builder gave `sha256:756e51cf…`, and the archive bytes of the wiped builder equal the earlier build (`.lab/evidence/m3/image-builds/`, `.lab/evidence/m3/archive-before-acceptance.sha256`). A pod that named the image by its manifest digest failed with `no such image` (`.lab/evidence/m3/image-import-probe.txt`).
- BuildKit's refusal of `rewrite-timestamp` with unpack: `.lab/evidence/m5/probe/03-rewrite-timestamp.txt`.

## 6. How the rules are enforced

- `tracks/b-ibsim/scripts/guest.sh` and `tracks/c-ns3/scripts/guest.sh` refuse with exit 2 unless the guest marker `/etc/rfl/track-b-guest` or `/etc/rfl/track-c-guest` exists, and then unless `systemd-detect-virt --vm` finds a virtual machine. The image entry points refuse without a read-only marker mount that only `guest.sh` adds. `images/rdma-tools/build.sh` refuses outside a virtual machine.
- `ansible/playbooks/track-b-guest.yml` and `track-c-guest.yml` install Docker from the Ubuntu archive at the versions of `ibsim.guest_docker` and its siblings and hold them, set `kernel.panic_on_oops=1` and `kernel.panic=5`, and write the marker. The guest profiles boot QEMU with `-no-reboot` and a serial log, so a panic ends the QEMU process and leaves the console in `serial.log`.
- Every simulator container runs with `--network none --cap-drop ALL --security-opt no-new-privileges --read-only --user 10001:10001 --pids-limit 256`, a tmpfs at `/tmp`, read-only inputs and one writable output mount. `container-locked-down` checks this in `docker inspect`, with AppArmor `docker-default`, the image digest, the guest kernel and the marker.
- `host-untouched` scans the host's process list and `docker ps` at the start and the end of every Track B and Track C run.

**Evidence.** Every `guest.sh` subcommand and every entry point, run on the host with stubs for docker, `systemd-detect-virt` and the simulator programs first on PATH, exited 2 with the refusal, and no call reached a stub (`.lab/evidence/m4/host-refusal.log`, `.lab/evidence/m5/host-refusal.log`, `.lab/evidence/m6/host-refusal.log`). `tests/unit/test_ibsim_host_refusal.py` and `tests/unit/test_ns3_host_refusal.py` repeat the refusal on every `make test`. After every Track B and Track C run of record the guest's serial log held no line matching `oops|BUG:|panic` (`.lab/evidence/m5/guest-serial-check.txt`, `.lab/evidence/m6/guest-serial-check.txt`). `container-locked-down` and `host-untouched` passed in runs `20261116T194712Z` and `20261116T200409Z`.
