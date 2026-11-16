# Track C fixtures

Every file here was captured from a real run inside the Track C guest `ns3sim1`. Nothing was edited.
The run directories are gitignored, so the run ids below are the record of where each file came from.

Unless a row says otherwise, the image is `rfl-ns3:m6`
`sha256:da7a3245b1b91dd472d1ddb9497b3bd1abb3efdbaee38c45fe790dff565a61c6`, on 2026-11-17 (UTC 2026-11-16).
The run of record is `20261116T151213Z`.

| Directory | Source | What it shows |
|---|---|---|
| `baseline/` | run `20261116T151213Z`, c-02-single-flow, `raw/run1/out/work/` and `raw/run1/in/network.txt`. `run2-run.tsv` is `raw/run2/out/work/run.tsv` | One flow alone. FCT 88092 ns, 1000 data packets of 1052 B, no drop, pause or mark |
| `rate-not-applied/` | run `20261116T151612Z`, break rate-not-applied, `raw/run1/` | Both links at 200 Gbps while the scenario declares 100 Gbps. FCT 46046 ns |
| `flow-cut-short/` | run `20261116T151617Z`, break flow-cut-short, `raw/run1/` | The simulator stops at 10 us. The flow does not complete |
| `lossy-links/` | run `20261116T151622Z`, break lossy-links, `raw/run1/` | 1% receive errors on every link device. 50 PhyRxDrop events, 40 NACKs |
| `ecn-bottleneck/` | run `20261116T151628Z`, break ecn-bottleneck, `raw/run1/` | Receiver link at 25 Gbps. 16 CE marks and 16 CNP-flagged ACKs |
| `pfc-bottleneck/` | run `20261116T151633Z`, break pfc-bottleneck, `raw/run1/` | Receiver link at 25 Gbps, 1 MB buffer, ECN off, one QP. 25 PAUSE and 25 RESUME, which the unpatched model could not survive |
| `mmu-drops/` | image `rfl-ns3:m5` `sha256:81d839a7…`, probe key `20261116T140300Z-mmu-drop-probe`, `out/work/` and the first 6 lines of `out/simulate.stdout` | PFC off at the hosts and a 200 kB buffer. The MMU prints Headroom full drops |
| `container/` | run `20261116T151213Z` c-02 `raw/run1/inspect.json` and `raw/run1/out/identity.txt`; run `20261116T151551Z` (break container-unlocked) `raw/run1/inspect.json` | docker inspect of a locked-down and of a writable-root container, and the source and patch record of the image |
| `incast/c-03-incast-simultaneous/` | run `20261116T151213Z`, `raw/run1/out/work/<case>/`, `raw/run1/out/<case>.stdout-summary` as `stdout-summary` and `raw/run1/in/<case>.txt` as `network.txt`, for the four profiles at fan-in 8 and 2, seed 1 | The four profiles on the same traffic. At fan-in 2 c-ecn and c-both give identical outputs |
| `incast/c-04-incast-staggered/n8-c-none-s1/` | run `20261116T151213Z`, as above | One flow stalls after 305000 B: the switch refused its tail and no NACK followed |
| `incast/c-05-incast-mixed/n8-c-ecn-s1/` | run `20261116T151213Z`, as above | 4 large and 16 small flows |
| `probe/` | image `rfl-ns3:m5`, the throwaway build probe of SimAI in `ns3sim1`, `/var/tmp/rfl-probe/simai-noble/run1/` | GNU time of the example, its `ncclFlowModel_EndToEnd.csv` and the tail of its stdout |
