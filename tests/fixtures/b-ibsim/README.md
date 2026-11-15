# Track B fixtures

Every file here is output captured from a real run. Nothing was typed by hand. All of it ran inside the Track B guest `ibsim1` of `tests/integration/qemu-lab/profiles/ibsim.tsv` (Ubuntu 24.04.5, kernel 6.8.0-147-generic, Docker 29.1.3). The tools ran in the image `rfl-ibsim:m4` `sha256:cb3e3550f4f60de5414abe55dbe81e45fd68106ef5ecc4cfa4d701b6083ee279`: ibsim 0.10-2build1, OpenSM 3.3.23-2, infiniband-diags 50.0-2ubuntu0.2. Run directories live under the gitignored `results/` of the machine that ran them.

Each file keeps the path it had under the run's `raw/` directory.

| fixture | source |
|---|---|
| `b-01/baseline/*`, `b-01/container/{inspect.json,top.txt,docker-run.txt}` | run `20261115T142123Z` b-01, passed, `raw/baseline/` and `raw/container/` |
| `b-03/{baseline,fault,recovered}/*`, `b-03/container/opensm.log` | run `20261115T142143Z` b-03, passed. `fault/` is after `Unlink "hca3"[1]`, `recovered/` after `ReLink "hca3"[1]` |
| `b-04/{baseline,fault}/*`, `b-04/container/opensm.log` | run `20261115T142204Z` b-04, passed. `fault/` is after `Unlink "cel1-ib-leaf-r3"[35]` |
| `b-02-updn-fallback/container/opensm.log` | run `20261115T141931Z` b-02, failed. It ran `opensm -R updn` on the single-tier B-02 fabric. updn found no root and OpenSM fell back to minhop |
| `guest-ps/fabric-running.txt` | `ps -eo pid=,comm=,args=` in `ibsim1` on 2026-11-16, while the container `rfl-ibsim-20261116T000000Z-capture-ps` ran `rfl-ibsim fabric` on `b-01.net`, started by `guest.sh start` |
| `guest-ps/fabric-stopped.txt` | the same command right after `guest.sh stop 20261116T000000Z-capture-ps` |

`ibroute.txt` holds one `ibroute <lid>` page per switch, each after a `$ ibroute <lid>` line the runner writes. `smpquery-nodedesc.txt` is the output of the runner's LID probe loop: `== <lid>`, the `smpquery nodedesc` output, then `rc=<exit code>`.
