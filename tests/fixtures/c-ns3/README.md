# Track C fixtures

Every file here was captured from a real run inside the Track C guest `ns3sim1`, on the image
`rfl-ns3:m5` with ID `sha256:81d839a74436b429f9439bc9039072818cb12a32cc4f23dbe001102695470713`,
on 2026-11-16. Nothing was edited. The run directories are gitignored, so the run ids below are the
record of where each file came from.

| Directory | Source | What it shows |
|---|---|---|
| `baseline/` | run `20261116T135412Z`, c-02-single-flow, `raw/run1/out/work/` and `raw/run1/in/network.txt`. `run2-run.tsv` is `raw/run2/out/work/run.tsv` | One flow alone. FCT 88092 ns, 1000 data packets of 1052 B, no drop, pause or mark |
| `rate-not-applied/` | run `20261116T135900Z`, break rate-not-applied, `raw/run1/` | Both links at 200 Gbps while the scenario declares 100 Gbps. FCT 46046 ns |
| `flow-cut-short/` | run `20261116T135905Z`, break flow-cut-short, `raw/run1/` | The simulator stops at 10 us. The flow does not complete |
| `lossy-links/` | run `20261116T135911Z`, break lossy-links, `raw/run1/` | 1% receive errors on every link device. 50 PhyRxDrop events, 40 NACKs |
| `ecn-bottleneck/` | run `20261116T135916Z`, break ecn-bottleneck, `raw/run1/` | Receiver link at 25 Gbps. 13 CE marks and 13 CNP-flagged ACKs |
| `pfc-bottleneck/` | run `20261116T140119Z`, break pfc-bottleneck, `raw/run1/` | Receiver link at 25 Gbps, 1 MB buffer, ECN off, four QPs. 25 PAUSE and 25 RESUME |
| `mmu-drops/` | probe key `20261116T140300Z-mmu-drop-probe`, `out/work/` and the first 6 lines of `out/simulate.stdout` | PFC off at the hosts and a 200 kB buffer. The MMU prints Headroom full drops |
| `container/` | run `20261116T135412Z` `raw/run1/inspect.json` and `raw/run1/out/identity.txt`; run `20261116T135839Z` (break container-unlocked) `raw/run1/inspect.json` | docker inspect of a locked-down and of a writable-root container |
| `probe/` | the throwaway build probe of SimAI in `ns3sim1`, `/var/tmp/rfl-probe/simai-noble/run1/` | GNU time of the example, its `ncclFlowModel_EndToEnd.csv` and the tail of its stdout |
