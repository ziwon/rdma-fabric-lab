# ADR 0006: The packet model of Track C

> Status: accepted, 2026-11-16. The patch of section 1.3 was the user's choice on 2026-11-16. Section 1.6 is open: it waits for the user's decision.

`docs/adr/0002-execution-environments.md` decides where Track C runs: the build and every simulator run in a container of a pinned image, in the Docker of the Track C guest. This ADR decides which model runs, how it is driven and which parameters the comparison uses.

## 1. Decision

1. **Backend.** SimAI's ns3 build path, `./scripts/build.sh -c ns3` unchanged, at SimAI `a57d765`, with ns-3-alibabacloud `3e0c7c1` and SimCCL `fd7cd57` at the gitlinks SimAI records. It builds on the Ubuntu 24.04 toolchain of the snapshot: gcc 13.3.0, CMake 3.28.3 and Python 3.12.3.
2. **Driver.** The repository's own `tracks/c-ns3/driver/rfl-star.cc` builds a star of N senders and one receiver around one switch from a rendered `network.txt`. The image build places it in the scratch directory of SimAI's ns-3 tree after SimAI's own build and builds it there. It reads every configured value back from the model objects and records what the model's trace sources saw. `c-01-example` runs SimAI's own quick smoke test unchanged.
3. **One patch.** `images/ns3/qbb-resume-qp-lookup.patch` is the only change to upstream source, pinned as `ns3.resume_patch`. `QbbNetDevice::Resume(qIndex)` read `m_qps[qIndex]`, the host's QP list indexed by the resumed priority, on every RESUME, although it needs the QP only for the NVLS test of node type 2. The patch tests the node type first. Wherever the old code did not crash it takes the same branch. The Dockerfile applies it with `git apply --check` and then `git apply`, so the build fails when it no longer applies.
4. **A profile sets three values and nothing else.**

   | Profile | `QbbEnabled` on every port | switch `EcnEnabled` | RdmaHw `CcMode` |
   |---|---|---|---|
   | c-none | false | false | 0 |
   | c-pfc | true | false | 0 |
   | c-ecn | false | true | 1 |
   | c-both | true | true | 1 |

   `same-traffic` checks that the network files of one fan-in differ only in the profile lines and the seed.
5. **Parameters.** The DCQCN values are those of SimAI's `astra-sim-alibabacloud/inputs/config/SimAI.conf` at `a57d765`, set on every `RdmaHw` and read back: `RateAI` 50 Mb/s, `RateHAI` 100 Mb/s, `MinRate` 100 Mb/s, `EwmaGain` 1/256, `RPTimer` 900 us, `RateDecreaseInterval` 4 us, `AlphaResumInterval` 1 us, `FastRecoveryTimes` 1, `ClampTargetRate` false. ECN kmin is 400000 B, kmax 1600000 B and pmax 0.2, SimAI.conf's values for 100 Gbps. The switch buffer is 4194304 B, SimAI.conf's 32 MiB divided by 8. PFC headroom is 3 × rate × delay per port, the pause time 5 us and the alpha shift 3. Every link is 100 Gbps and 1 us, and every flow has priority 3.
6. **No switch without PFC yet.** At this commit the switch sends PAUSE in every profile. A switch that uses its whole buffer and sends no PAUSE needs a second change to `SwitchNode`: an attribute that skips `CheckAndSendPfc` and `CheckAndSendResume` and lets `CheckIngressAdmission` admit up to the free buffer. It was not made. Whether to make it is the user's decision.
7. **Each case runs twice.** Every scenario runs `run1` and `run2` with the same inputs in two containers, and `rerun-identical` requires the same output files byte for byte, the simulator's own wall time and RSS aside.

## 2. Reasons

- Two candidates were probed in throwaway containers in the Track C guest. SimAI's path built in 62 s and ships an RDMA example that ran twice with byte-identical results. ns-3-alibabacloud alone built in 57 s but ships only `scratch-simulator`, which models no network. Its flow-file driver `NormalNetwork.cc` exists only on the `dev/qp` and `dev/hpn7.0` branches. SimAI's ns-3 tree is the same `3e0c7c1` the standalone candidate would build (`.lab/evidence/m5/probe/02-simai-noble-inner.log`, `04-standalone-noble-inner.log`, `00-source-reading.txt`). Both need gcc 9.4, CMake 3.14 and Python 3.8 or newer and no ninja, which Ubuntu 24.04 meets, so no older userspace is needed.
- SimAI's guide runs `git submodule update --remote`, which follows moving branches, so the image fetches each repository at its pinned commit instead (`docs/adr/0002-execution-environments.md` section 5).
- An incast with PFC on and one QP per sender hits the RESUME crash. A probe with one, three and four QPs on priority 3 crashed, crashed and completed (`.lab/evidence/m5/probe/05-pfc-resume-qp-index.txt`). The alternative, priority + 1 QPs per host, would change the traffic of every case. The user chose the patch.
- The three values are all a profile can change in this model. `QbbEnabled` decides only whether a host acts on a received PAUSE (`qbb-net-device.cc:492`). The switch's MMU sends PAUSE and RESUME in every profile (`switch-node.cc:92-132`). `CcMode` 0 matches none of the model's congestion control branches. Two configuration names reach no decision at all: `QbbNetDevice` `DynamicThreshold` is stored and never read, and `QcnEnabled` is read only behind a node type test that never holds (`.lab/evidence/m5/unused-attributes.txt`).
- The model defaults of DCQCN (`RateAI` 5 Mb/s, `RateHAI` 50 Mb/s, `EwmaGain` 1/16, `RPTimer` 1500 us) are not SimAI.conf's. SimAI ships its configuration with this model, so the comparison uses it. With a 32 MiB buffer the PFC threshold at fan-in 8 lies near 2 MB per sender, above every queue these 1 MB flows build, and PFC would never trigger. With 4 MiB it lies at 0.4 MB per sender at fan-in 2 and 0.24 MB at fan-in 8, inside the range the workloads reach. This is a choice for the comparison, not a recommended switch buffer.
- A comparison of PFC on and off is fair only as hosts that obey PAUSE against hosts that ignore it, behind the same switch. A lossy switch needs code that this ADR does not add without a decision.

## 3. Evidence

- The crash: the first `pfc-bottleneck` run died with SIGSEGV, exit 139, before writing any counter (run `20261116T135921Z`). With the patch, the four-QP case is byte-identical to the unpatched build, and the one-QP case completes at 340107 ns with 25 PAUSE and 25 RESUME acted on (`.lab/evidence/m6/patch-proof/patch-proof.txt`).
- `c-01-example` to `c-06-sensitivity-kmin` passed in run `20261116T151213Z` (`.lab/evidence/m6/20261116T151213Z/`) and again from a wiped guest with an image built with no cache in run `20261116T200409Z`. `c-01` simulated 0.38940403 s and `c-02` completed at 88092 ns in both, the same figures as before the patch.
- Across the 200 incast cases of `c-03` to `c-06`, with PFC on the host pause handler ran once for every PAUSE and RESUME and the MMU refused no packet. With PFC off the handler never ran although hosts received PAUSE frames. With ECN off there was no mark and no rate change. With ECN on no mark fell at or below kmin and every dequeue above kmax was marked.
- The site's Track C lab pages hold the comparison tables, the FCT decomposition of `c-02`, the rate trace that explains the long FCTs of the ECN profiles, and what each result does and does not show.
