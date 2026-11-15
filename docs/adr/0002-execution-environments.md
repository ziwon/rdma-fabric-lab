# ADR 0002: Execution environment per track

> Status: accepted, 2026-11-15

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
