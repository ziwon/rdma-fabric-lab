# ADR 0005: The InfiniBand management simulation of Track B

> Status: accepted, 2026-11-16. B-05, the SM restart and standby SM scenario, is optional and has not run.

`docs/adr/0002-execution-environments.md` decides where Track B runs: one container of a pinned image in the Docker of the Track B guest, never on the host. This ADR decides what runs in it.

## 1. Decision

1. **Ubuntu packages, no source build.** The image installs ibsim 0.10-2build1 (`ibsim-utils`, `libumad2sim0`), OpenSM 3.3.23-2 and infiniband-diags 50.0-2ubuntu0.2 from the archive snapshot of the lock.
2. **One container per run holds the whole fabric.** ibsim, OpenSM and every diagnostic tool run in it as UID 10001. `LD_PRELOAD=libumad2sim.so` is set per process for OpenSM and each tool, never image wide. The runner `python -m ibsim` on the host reaches the guest over SSH and never starts a simulator, a tool or a container itself.
3. **Three topologies.** `b-01.net` is 2 HCAs on one switch after upstream `net.1`. `b-02.net` is 4 HCAs on two switches joined by two parallel links after upstream `net.2sw2path4hca`. `cel1-compute.net` is the cel1 compute fabric, rendered by `make track-b-render` from `fabrics.compute` of `sites/cel1.yaml`: 12 switches, 64 single-port HCAs named `<host> <hca>`, 768 switch ports and 128 links. `make track-b-render-check` fails when the file differs from what the site model renders.
4. **One routing engine per scenario.** B-01 to B-03 route with minhop. B-04, the two-tier cel1 fabric, routes with updn. Each scenario passes its engine as `opensm -R`, and `subnet-up` fails a run whose log names any other engine.
5. **One SM with LMC 0.** Every linked HCA port must get a non-zero LID, and no two ports may share one.
6. **A break removes one link.** `python -m ibsim run --break` gives the simulator the net file without the scenario's `break` link of `tracks/b-ibsim/config/scenarios.yaml`, while every expectation keeps the full topology file. The cut file is saved as `raw/break/simulated.net` and recorded as an input with its checksum.

## 2. Reasons

- The probe found all four packages in noble and built nothing from source (`.lab/evidence/m4/probe/03-packages.txt`). Upstream ibsim 0.12 exists, but no Ubuntu package carries it, and 0.10 runs every scenario.
- ibsim delivers MADs over its socket through libumad2sim. A preload set image wide would reach processes that must not talk to the simulator.
- updn needs root switches. On the single-tier fabrics of B-01 to B-03 it logs `disabling UPDN algorithm, no root nodes were found` and falls back to minhop (runs `20261115T141931Z` and `20261115T141940Z`). Every route there crosses at most one switch-to-switch link, so minhop cannot form a credit loop. That is reasoning, not a measurement.
- Up/down routing is free of credit loops on any topology, and in a two-tier fabric where each leaf reaches each spine its paths are shortest. ftree reached SUBNET UP on the healthy cel1 fabric but rejected the degraded one with `ERR AB0B` and fell back to minhop (`.lab/evidence/m4/probe/12-engines-under-fault.txt`). updn routed through the fault and the recovery.
- OpenSM 3.3.23 rejects the `no_fallback` engine option (`.lab/evidence/m4/probe/10-routing-engines-cel1.txt`). A failed engine therefore always falls back and OpenSM keeps running, so only the log shows which engine routed.
- Keeping the expectations on the full topology makes a missing link fail discovery and port states. The site model stays the single source of the cel1 fabric, and the topology file can never drift from it.

## 3. Evidence

- B-01 to B-04 passed 10, 10, 16 and 16 checks in run `20261115T151655Z`. In B-04, 13 routes left through the two ports of the failed link, all 13 moved to other ports, all 76 LIDs still answered, and the recovery restored the baseline. The latest run, `20261116T194712Z`, passed on `ibsim.image` `sha256:e4374db2…`.
- Every scenario failed on its break in run `20261115T151828Z` and again in `20261116T194838Z`. `routes-shortest` still passed in every break run, which is correct: with one link gone, each route still leaves by a port one hop closer to its endpoint.
- A mutation check changed one line in each of eight places, among them the route direction, duplicate LIDs, link symmetry and the QEMU flag allowlist, and each change failed at least one unit test (`.lab/evidence/m4/unit-mutations.txt`). The unit tests recompute the cel1 counts from the raw site model and rebuild every link from its cable list.

## 4. What it cannot show

ibsim reports every link as 4X SDR at 2.5 Gbps, so link speed, width negotiation and physical errors are not simulated. No ConnectX HCA, switch firmware, VF GUID or SR-IOV path exists here. MADs cross a Unix socket, so no RDMA traffic and no IPoIB ran. Partitions, QoS, multicast and SM failover were not checked.
