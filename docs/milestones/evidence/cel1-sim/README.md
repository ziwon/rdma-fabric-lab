# cel1 simulation evidence

`docs/milestones/cel1-sim.md` cites these files.

| Path | Content |
|---|---|
| `host-before-boot.txt`, `host-after-boot.txt` | The host before the first guest booted and with the 14 development guests up |
| `contract-runtime-check.log` | The contract check of the cel1 inventory, and of a copy with `crun` as the DGX runtime |
| `reproduce-and-break.log` | Both generators rerun with sha256 compared, and a break of each committed output |
| `lab-up-1.log`, `stand-ins-1.log`, `fleet-*.log` | The development run on fresh guests, before and after the fixes of section 7 |
| `label-watch.log` | The DGX opt-out labels every 30 seconds for 12 minutes, then the GPU Operator log lines about nodes and labels |
| `acceptance/` | The acceptance run `20261115T163759Z` from wiped guests. `steps.tsv` holds each step with its expected and real exit code. The logs of the Ansible and Helm steps keep only the commands, PLAY RECAP, release and exit lines. `strace-summary.txt` counts the traced calls of each refusal |
| `<run id>/cel1/cel1-sim-baseline/` | The manifest, metrics and summary of the three acceptance runs of `cel1-sim-baseline`. The raw captures stay in `results/` |
| `refusals-final.log` | The ten refusals of the fleet targets on the final code, with the script that ran them |
| `regression/` | The step and run tables of the M2 and ha acceptances after the changes |
| `gates/` | `make lint`, `make test`, `make validate`, `make fleet-check` and `make track-b-render-check`, with `exit-codes.txt` |
| `guests-stopped.log`, `wipe.log`, `space-guard.txt` | Every guest stopped, the wipe of the cel1 guest disks with the space it freed, and the free-space guard samples |
