# cel1 simulation fixtures

Captured on 2026-11-16 from the guests of the cel1 simulation (`LAB_PROFILE=cel1`), after
`make fleet-k8s`, `make fleet-addons` and `make fleet-rsyslog` with `CONFIRM=cel1` had run. Every
address in them is an invented cel1 address or a QEMU user network address.

| File | Command | Guest |
|---|---|---|
| `cilium-status-dgx-01.txt` | `kubectl -n kube-system exec <agent> -c cilium-agent -- cilium-dbg status` | the Cilium agent on cel1-dgx-01 |
| `containerd-config-dump-dgx-01.toml` | `sudo /usr/local/bin/containerd config dump` | cel1-dgx-01 |
| `containerd-config-dump-cp-01.toml` | `sudo /usr/local/bin/containerd config dump` | cel1-cp-01 |
| `ip-addr-dgx-01.json` | `ip -j -4 addr show` | cel1-dgx-01 |
| `ss-established-dgx-01.txt` | `ss -Htn state established` | cel1-dgx-01 |
| `ip-link-type-vxlan-dgx-02.json` | `ip -d -j link show type vxlan` | cel1-dgx-02, which has no VXLAN link |
| `kubeadm-flags-dgx-02.env` | `sudo cat /var/lib/kubelet/kubeadm-flags.env` | cel1-dgx-02 |
