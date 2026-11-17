# VMware Workstation lab VM specification

> Status: **not verified on this host.** The verification host has no VMware Workstation. The verification applied the same Ansible code to unprivileged QEMU/KVM guests (`tests/integration/qemu-lab/`). This document keeps the VMware Workstation path of the lab architecture as a manual procedure.

## 1. Scope

VM creation is manual. Ansible handles only VMs that already exist. This document produces three Ubuntu Server 24.04 LTS VMs reachable over SSH and one override inventory that points at them.

The RXE scenarios use two VMs, `worker1` and `worker2`. `cp1` joins them for Kubernetes.

## 2. VM resources

These values are the initial allocation of the lab resource budget. They are not a verified minimum specification. `make doctor` compares the host CPU, memory and disk with these values and warns.

| VM | vCPU | Memory | Disk | NIC |
|---|---:|---:|---:|---|
| cp1 | 2 | 4GiB | 30GiB | 1 management NIC |
| worker1 | 4 | 6GiB | 40GiB | Management NIC, RDMA NIC |
| worker2 | 4 | 6GiB | 40GiB | Management NIC, RDMA NIC |

The guest OS is Ubuntu Server 24.04 LTS. Record the date and checksum of the installation media. `lab.guest_image` in `versions.lock.yaml` pins the cloud image the QEMU harness used. If the VMware install uses an ISO, record that ISO's checksum separately.

`lab.guest_kernel` pins the running kernel. If `uname -r` differs from the lock value after installation, `make bootstrap` stops. In that case, first check whether a package provides `rdma_rxe` for that kernel, then pin the lock again.

## 3. Two VMnets

| Use | VMnet type | Subnet | DHCP | Default route |
|---|---|---|---|---|
| Management network | NAT (for example VMnet8) | `192.168.140.0/24` | On. Reserve the static addresses `.11`–`.13` or keep them outside the DHCP range | Only on this network |
| RDMA lab network | Host-only (for example VMnet2) | `10.77.0.0/24` | Off | None |

1. Run `make doctor` first. doctor fails if the two subnets above or the Pod and Service CIDRs overlap host addresses, routes or container networks. On an overlap, change `lab_address_plan` in `ansible/inventories/lab/group_vars/all/lab.yml` and the node addresses in `hosts.yml` together.
2. In the Virtual Network Editor (`vmware-netcfg`), set the subnet of the NAT VMnet to `192.168.140.0/24`. Do not overwrite an existing VMnet configuration. Use a free number.
3. Add one Host-only VMnet and set its subnet to `10.77.0.0/24`. Turn off "Use local DHCP service". This keeps DHCP from colliding with the Multus address pool `10.77.0.100`–`10.77.0.199` and the static VM addresses.
4. The Host-only VMnet does not need a host adapter. If you connect one, the host gets a `10.77.0.0/24` address, so run `make doctor` again to check.

## 4. VM NICs

- Connect the first NIC to the management NAT VMnet.
- Connect the second NIC to the RDMA Host-only VMnet. Only `worker1` and `worker2` have it.
- Record the NIC type. `make preflight` reports the driver name (`vmxnet3`, `e1000e` and others) inside the guest.
- Give the second NIC no address during installation. The `network` role sets its static address with the netplan file `/etc/netplan/60-rfl-rdma.yaml`.
- Start with MTU 1500. MTU change experiments go in a separate profile.

NIC names are inventory variables. Do not assume `ens33` and `ens37`. Check the names in the guest with `ip -br link`. Match them against `ethernetN.generatedAddress` in the `.vmx` file or the MAC in the VM settings to decide which NIC is on which VMnet.

## 5. Guest preparation

1. Set static management IPs. They are `cp1` `192.168.140.11`, `worker1` `192.168.140.12` and `worker2` `192.168.140.13`.
2. Create the lab user and install its SSH public key. `make bootstrap` uses `become`, so either give that user passwordless sudo or run `ansible-playbook` directly with `-K`.
3. SSH from the host into each VM once to add its host key to `known_hosts`. `ansible.cfg` does not turn off host key checking.
4. Check that the network renderer is systemd-networkd. This is the Ubuntu Server default. The `network` role rejects NetworkManager guests.

## 6. Override inventory

Connection details and NIC names go in as a second `-i` after `ansible/inventories/lab/`. The QEMU harness creates this file as `.lab/inventory.yml`. A VMware lab keeps a file of the same shape outside the repository.

```yaml
all:
  hosts:
    cp1:
      ansible_user: lab
      lab_mgmt_nic: ens33
    worker1:
      ansible_user: lab
      lab_mgmt_nic: ens33
      lab_rdma_nic: ens37
    worker2:
      ansible_user: lab
      lab_mgmt_nic: ens33
      lab_rdma_nic: ens37
```

The NIC names above are examples that show the format. Find the real names with the method in section 4. `ansible_host` uses the management IP from `hosts.yml` unchanged.

Give the RDMA NIC the same name on both workers, for example with a systemd `.link` file or a netplan `set-name` on its MAC. The Multus NetworkAttachmentDefinition names one macvlan master for every node and takes it from `lab_rdma_nic`, and `python -m run.multus render` refuses when the workers disagree. The QEMU harness renames the NIC `lab0` at first boot for the same reason (section 8).

```bash
make doctor
make versions SCOPE=lab
make bootstrap LAB_OVERRIDE=$HOME/rdma-lab/vmware-inventory.yml
make preflight LAB_OVERRIDE=$HOME/rdma-lab/vmware-inventory.yml
make rxe-up    LAB_OVERRIDE=$HOME/rdma-lab/vmware-inventory.yml
make rxe-test  LAB_OVERRIDE=$HOME/rdma-lab/vmware-inventory.yml
make k8s       LAB_OVERRIDE=$HOME/rdma-lab/vmware-inventory.yml LAB_API_URL=
make addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig
make k8s-test  LAB_OVERRIDE=$HOME/rdma-lab/vmware-inventory.yml
make rdma-tools-load      LAB_OVERRIDE=$HOME/rdma-lab/vmware-inventory.yml
make k8s-hostnet-test    LAB_OVERRIDE=$HOME/rdma-lab/vmware-inventory.yml
make k8s-multus-test     LAB_OVERRIDE=$HOME/rdma-lab/vmware-inventory.yml
```

`make rdma-tools-load` imports the archive that `make rdma-tools-image` builds. That build runs in the Track B guest of the QEMU harness, so it needs `/dev/kvm` on the machine that builds the image.

In a VMware lab, one management NIC carries the default route, SSH and Kubernetes. So there is no `lab_uplink_nic`. The host reaches `192.168.140.11:6443` directly through the host adapter of the NAT VMnet, so `LAB_API_URL` stays empty and the kubeconfig address stays as it is. Section 8 lists the differences from the QEMU harness.

## 7. Not confirmed on this path

- Whether the VMware Host-only VMnet forwards RoCEv2 UDP 4791 frames between the two guests.
- RXE performance and error behavior on vmxnet3 and e1000e.
- Whether the Host-only VMnet passes the extra MAC addresses of macvlan children. `tests/integration/qemu-lab/probe-extra-mac.sh macvlan` is the probe the harness ran. If they do not pass, ipvlan L2 is the IP alternative.
- Whether disconnecting a VMware NIC shows up in the guest as carrier loss. In the QEMU harness, systemd-networkd removes the static address of the RDMA NIC when carrier goes away (`docs/troubleshooting.md` section 5).

- Whether the NAT VMnet forwards Cilium VXLAN (UDP 8472) between the three guests, and whether the host reaches the API.

The result directories of the first `make rxe-test` and `make k8s-test` runs on a VMware host decide these items.

## 8. How the QEMU harness differs

The verification ran on the QEMU harness. These are the differences a VMware lab must keep in mind when it compares results.

| Item | VMware network design | QEMU harness |
|---|---|---|
| Management network | One NAT VMnet carries internet, SSH and the Kubernetes API | Split in two. A QEMU user NIC per guest (`10.0.2.0/24`, DHCP, default route, SSH and package downloads), and a management network the guests share (`192.168.140.0/24`, static addresses, Kubernetes and VXLAN) |
| Management network L2 | The VMware virtual switch | `switch.py`, a learning switch with user privileges that moves frames over AF_UNIX datagram sockets. Frames cannot leave the host, and the process has no capability (`CapEff: 0000000000000000`) |
| Management address | Set during VM installation | cloud-init network-config sets it at first boot. Ansible does not touch the management NIC |
| Default route and SSH | The management NIC | The user NIC, named by `lab_uplink_nic`. A VMware lab leaves that variable unset |
| API from the host | `192.168.140.11:6443` through the host adapter of the NAT VMnet | Port 6443 of cp1 is forwarded to `127.0.0.1:22643`. `make k8s` sets the server of the fetched kubeconfig to that address and `tls-server-name` to `kubernetes` |
| RDMA lab network | The Host-only VMnet | A direct worker1 to worker2 cable of two AF_UNIX datagram sockets. It passes any MAC address, so macvlan children cross it |
| NIC names | Checked after installation and set in the override inventory | Found by MAC and written to the override inventory. A cloud-init `set-name` on its MAC renames the lab NIC of both workers `lab0` at first boot, and a profile that declares a name gets it the same way (`mgmt0` in the ha profile) |
| Guest disks | A VMware datastore | qcow2 overlays under `LAB_DIR` on the pinned cloud image in `.lab/images/` |
| RDMA test image | Not built by VMware | Built in the Track B guest, booted beside the lab by `make rdma-tools-image`, and imported into each worker's containerd without a registry by `make rdma-tools-load` |
| Power failure | Power off the VM | `lab.sh kill` sends SIGKILL to QEMU, so the guest gets no shutdown sequence |

The split default route of the harness is the condition behind the Cilium API defect of `docs/adr/0001-kubernetes-stack.md` section 9. On a VMware lab with one management NIC the condition does not arise, and the fix still holds.
