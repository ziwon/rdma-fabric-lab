# M3 evidence

The files in this directory are copies of results from runs in the QEMU harness on this host on 2026-11-15 and 2026-11-16 (UTC). The originals remain in the gitignored `results/` and `.lab/`. No value was made by hand.

## What changed in the copy

- The absolute repository path became `<repo>`, the home directory `~` and the user name `<user>`. The copy script confirmed that neither remains.
- Files named `*.log` under `acceptance-m3/` for steps 10, 11 and 12, and the files under `image-builds/`, hold only the lines that carry results: the helm and kubeconform command lines, the release lines, the kubeconform summaries and the build result lines.
- Run directories under `runs/` and `breaks-runs/` hold the manifest, the summary and the metrics, and for a few runs single raw files. The copies have no full `raw/`, so `rfl validate` cannot check them. The contract check ran with `make validate` against the original directories.

## Files

| File | Original |
|---|---|
| `host-before-boot.txt` | `.lab/m3/host-before-boot.txt`. Cores, memory and disk before the first boot |
| `probe-macvlan.log` | `.lab/probe-m3/20261115T215418Z-macvlan/probe.log`. The extra-MAC probe |
| `cni-netd-before-multus.txt`, `cni-netd-after-multus.txt` | `/etc/cni/net.d` of every node with Cilium alone, then with Multus and Whereabouts |
| `pod-security-probe.txt` | The pod security profiles tried, with their errors |
| `image-import-probe.txt` | `crictl inspecti` by the index digest and by the manifest digest on worker1 |
| `image-builds/` | The five `make rdma-tools-image` runs before the acceptance run |
| `archive-before-acceptance.sha256`, `acceptance-m3/archive-after-build.sha256` | sha256 of the image archive before and after the rebuild in the wiped builder |
| `addons-first.log`, `addons-second.log` | The first `make addons` with all three lab releases, and the second, unchanged |
| `shadow/` | worker2 after the boot-time shadow break: the stale `00-multus.conf`, the Multus log lines, the restart of its pod and the regenerated file |
| `acceptance-m3/` | `.lab/acceptance-m3/20261116T001921Z/`. The 40 steps, the runs of record and the guest list before each of them |
| `acceptance-ha/` | `.lab/acceptance-ha/20261115T224355Z/`. The ha profile acceptance after the M3 changes |
| `acceptance-m2/` | The M2 acceptance after the M3 changes |
| `breaks-{1,2,3}/` | The three series of `breaks-m3.sh`: steps and break pairs |
| `breaks-runs/` | The summary of each broken run of the pairs |
| `runs/` | The runs of record of section 7 of M3.md, the first failed Multus run, the memlock run of section 5 and the ha run |
| `cel1-unchanged/compare.log` | The byte comparison of `make addons-template ENV=cel1` and `make fleet-check` with the output saved before any edit |
| `gates/` | The exit codes of the gates |
| `cleanup.log` | The deletion of `/data/rfl-lab/m3` after every guest stopped |
