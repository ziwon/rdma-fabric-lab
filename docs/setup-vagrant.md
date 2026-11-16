# Vagrant lab on VMware Workstation

> Status: **phase 1.** Every step that needs VMware or Vagrant has not run on the lab host yet. The steps that ran say so and cite their evidence under `docs/milestones/evidence/vagrant/`, which git does not keep. The rest carry **Not run on the lab host.**

## 1. Scope

This path builds the same three lab guests as the QEMU harness (`tests/integration/qemu-lab/`) on VMware Workstation Pro, with Vagrant. It ends with the override inventory `.lab/vagrant/inventory.yml`, so every existing make target from `make bootstrap` to `make k8s-multus-test` runs unchanged with `LAB_OVERRIDE` pointing at it.

`docs/setup-vmware.md` keeps the manual VMware path. The two paths differ as follows.

| Topic | `docs/setup-vmware.md` | This document |
|---|---|---|
| Guests | Created by hand in Workstation | Created by `make vagrant-up` from `vagrant/Vagrantfile` |
| Guest OS | Installed by hand from an ISO or image | The box `make vagrant-box` builds from `lab.guest_image`, so the guests boot `lab.guest_kernel` |
| Management network | The NAT VMnet set to `192.168.140.0/24`, which also carries SSH and the default route | A host-only vmnet `192.168.140.0/24` without DHCP. The NAT vmnet only carries SSH and the default route, as the user NIC of the QEMU harness does |
| Lab network | A host-only VMnet made by hand | A host-only vmnet `10.77.0.0/24` without DHCP that Vagrant creates |
| NIC names | Found and renamed by hand | The seed renames the lab NIC `lab0`. `make vagrant-up` finds the other names by MAC |
| Override inventory | Written by hand | Written by `make vagrant-up` |

Sizes, addresses and NIC roles come from two files only. `tests/integration/qemu-lab/profiles/lab.tsv` gives the guests, their vCPUs, memory, disk and NICs. `ansible/inventories/lab` gives every address. `python -m vagrant plan` derives the Vagrantfile input from both, and `tests/unit/test_vagrant_vagrantfile.py` checks that the Vagrantfile holds no address, size or MAC of its own.

| Guest | vCPU | Memory | Disk | NICs |
|---|---:|---:|---:|---|
| cp1 | 2 | 4096 MiB | 30 GiB | NAT, management |
| worker1 | 4 | 6144 MiB | 40 GiB | NAT, lab `lab0`, management |
| worker2 | 4 | 6144 MiB | 40 GiB | NAT, lab `lab0`, management |

The order is fixed. Install VMware Workstation first (section 2), then Vagrant, the VMware Utility and the plugin (section 3). The utility service starts at install and drives VMware, so it comes after Workstation.

## 2. VMware Workstation Pro on Ubuntu 24.04

`versions.lock.yaml` pins `vagrant.vmware_workstation` to 26H1u1 build 25688693. It is free for all use and needs no license key.

### 2.1 Build tools and kernel headers

vmmon and vmnet are built on the host against the running kernel. The compiler must be the one that built that kernel.

```bash
sudo apt update
sudo apt install build-essential linux-headers-$(uname -r)
cat /proc/version       # names the gcc that built the running kernel
gcc --version
```

A read-only check ran on the lab host on 2026-11-16 (`docs/milestones/evidence/vagrant/host-facts.txt`). The kernel 7.0.0-43-generic was built with gcc 13, `gcc --version` printed 13.3.0, and `/usr/src/linux-headers-7.0.0-43-generic` exists. **The apt step was not run on the lab host.**

### 2.2 Install the bundle

1. Sign in to the Broadcom Support Portal with a free account and download VMware Workstation Pro 26H1u1, build 25688693, for Linux. It is one `.x86_64.bundle` file.
2. Compare its SHA256 with the value the portal lists for that file.

   ```bash
   sha256sum VMware-Workstation-*.x86_64.bundle
   ```

3. Install it.

   ```bash
   chmod +x VMware-Workstation-*.x86_64.bundle
   sudo ./VMware-Workstation-*.x86_64.bundle --console --required --eulas-agreed
   vmware-installer -l
   ```

**Not run on the lab host.** Nobody here has seen the portal listing, so `vagrant.vmware_workstation` pins the build number and not the bundle bytes.

### 2.3 Build vmmon and vmnet

```bash
sudo vmware-modconfig --console --install-all
modinfo -n vmmon vmnet
```

**Not run on the lab host.** Kernel 7.0 is newer than many Workstation releases. If the build fails, keep the compiler output and stop here. With Secure Boot on, the modules build but do not load until section 2.4 signs them. `modprobe vmmon` then fails with `Key was rejected by service`.

### 2.4 Sign the modules for Secure Boot

The lab host runs with Secure Boot enabled (`mokutil --sb-state`, `host-facts.txt`). The kernel loads only modules signed by a key in its trust list, so vmmon and vmnet need a machine owner key (MOK).

Use a key that is already enrolled when you hold its private key. Ubuntu's DKMS keeps one under `/var/lib/shim-signed/mok/`. If `sudo mokutil --test-key /var/lib/shim-signed/mok/MOK.der` reports that the key is already enrolled, use `MOK.priv` and `MOK.der` from there and skip to the signing step. No reboot is needed then.

Otherwise create a key and enroll it. Enrollment finishes only in the MokManager screen at the next boot.

Enrolling a key only adds it to the trust list. The kernel and modules that already boot keep booting. If MokManager times out, the machine boots normally without the key and `mokutil --import` can run again. The risk at this reboot is elsewhere: the installer starts `vmware.service` at boot (`S01vmware` in `/etc/rc2.d` to `rc4.d`), and that service loads vmmon and vmnet. Once they are signed, a module that faults while loading on a host with `kernel.panic_on_oops=1` and `kernel.panic=10` panics the kernel, reboots after 10 s and faults again on the next boot. So before this reboot, keep the service from starting, and make the GRUB menu reachable in case an older kernel or recovery mode is needed:

```bash
sudo systemctl disable vmware
sysctl kernel.panic_on_oops kernel.panic
grep -E '^GRUB_TIMEOUT(_STYLE)?=' /etc/default/grub   # hidden and 0 means hold Shift or press Esc during boot
```

After the boot, load the modules by hand once (`sudo modprobe vmmon && sudo modprobe vmnet && sudo systemctl start vmware`). Enable the service again with `sudo systemctl enable vmware` only after that worked. If the load faults, the machine reboots once and comes up without VMware, and the oops is in `/var/lib/systemd/pstore/`.

```bash
openssl req -new -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=VMware module signing/" \
  -keyout MOK.priv -outform DER -out MOK.der
sudo mokutil --import MOK.der   # asks for a one-time password
sudo reboot                     # MokManager: Enroll MOK, Continue, Yes, the password
```

Sign both modules and load them.

```bash
for m in vmmon vmnet; do
  sudo /usr/src/linux-headers-$(uname -r)/scripts/sign-file sha256 MOK.priv MOK.der "$(modinfo -n $m)"
done
sudo modprobe vmmon
sudo modprobe vmnet
sudo systemctl restart vmware
grep -E '^(vmmon|vmnet) ' /proc/modules
```

**Not run on the lab host.** Every kernel update rebuilds the modules unsigned, so repeat sections 2.3 and 2.4 after one. Keep `MOK.priv` readable by root only.

### 2.5 KVM and VMware on one host

Linux 6.12 made KVM turn on hardware virtualization when its module loads, controlled by `kvm.enable_virt_at_load`. Kernel bug 219602 reports that this default breaks other hypervisors (`https://bugzilla.kernel.org/show_bug.cgi?id=219602`). VirtualBox fails beside it, and the known workaround is `enable_virt_at_load=0`. No source found so far confirms the same failure for VMware Workstation.

On the lab host, `kvm_intel` is loaded and `/sys/module/kvm/parameters/enable_virt_at_load` reads `Y` (`host-facts.txt`). The file is read-only at run time, so the value changes only when the module loads again.

1. Stop every QEMU guest, for example with `make lab-down`.
2. Set the parameter and load KVM again.

   ```bash
   echo "options kvm enable_virt_at_load=0" | sudo tee /etc/modprobe.d/kvm-virt-at-load.conf
   sudo modprobe -r kvm_intel kvm
   sudo modprobe kvm_intel
   cat /sys/module/kvm/parameters/enable_virt_at_load    # N
   ```

**Not run on the lab host.** Nobody has confirmed that VMware needs this step. It costs QEMU nothing, because with `N` KVM turns on virtualization when a QEMU guest starts and holds VT-x only while one runs. The two hypervisors still never run guests at the same time. `make vagrant-up` refuses while any `qemu-system` process runs. It matches the first word of each process command line, because the harness renames its QEMU processes `rfl-lab-<guest>`, which `pgrep qemu-system` does not find. `make lab-up` does not check for VMware guests, so run `make vagrant-down` before it. Without the parameter, `sudo modprobe -r kvm_intel kvm` before VMware and `sudo modprobe kvm_intel` before QEMU also alternate the two.

## 3. Vagrant, the VMware Utility and the plugin

### 3.1 Vagrant from the HashiCorp repository

```bash
wget -O hashicorp-apt.asc https://apt.releases.hashicorp.com/gpg
gpg --show-keys --with-fingerprint hashicorp-apt.asc
sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg hashicorp-apt.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(grep -oP '(?<=UBUNTU_CODENAME=).*' /etc/os-release || lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update
sudo apt install vagrant=2.4.9-1
sudo apt-mark hold vagrant
vagrant --version
```

`gpg --show-keys` must print the fingerprint `D55C 0D1A C78A 8D81 26CB 631C FC9C A96A CA02 6560`. That is the key the URL served on 2026-11-16. The same day, the noble `Packages` file of the repository listed `vagrant` 2.4.9-1, which `vagrant.vagrant_package` pins. The hold keeps apt from moving past the lock. `make vagrant-up` refuses any `vagrant --version` other than `vagrant.vagrant`.

The fingerprint and the package list were read on the lab host. **The install was not run on the lab host.**

### 3.2 Vagrant VMware Utility

The utility is a root service on `127.0.0.1:9922` that the plugin calls for every VMware action, such as creating a vmnet.

```bash
v=1.0.24
base=https://releases.hashicorp.com/vagrant-vmware-utility/$v
curl -fLO "$base/vagrant-vmware-utility_${v}-1_amd64.deb"
curl -fLO "$base/vagrant-vmware-utility_${v}_SHA256SUMS"
curl -fLO "$base/vagrant-vmware-utility_${v}_SHA256SUMS.sig"
export GNUPGHOME=$(mktemp -d)
curl -fsSL https://www.hashicorp.com/.well-known/pgp-key.txt | gpg --import
gpg --fingerprint security@hashicorp.com
gpg --verify "vagrant-vmware-utility_${v}_SHA256SUMS.sig" "vagrant-vmware-utility_${v}_SHA256SUMS"
sha256sum --check --ignore-missing "vagrant-vmware-utility_${v}_SHA256SUMS"
sudo apt install "./vagrant-vmware-utility_${v}-1_amd64.deb"
systemctl status vagrant-vmware-utility
```

`gpg --fingerprint` must print `C874 011F 0AB4 0511 0D02 1055 3436 5D94 72D7 468F`, and `gpg --verify` must report a good signature. The `.deb` line of the checksum file equals `vagrant.vmware_utility_package` in `versions.lock.yaml`.

The download and both checks ran on the lab host on 2026-11-16 and passed (`docs/milestones/evidence/vagrant/utility-verify.log`). **The install was not run on the lab host.**

### 3.3 The plugin

The make targets run Vagrant with `VAGRANT_HOME=$(VAGRANT_DATA)/home`, by default `/data/rfl-vagrant/home`, so the box and the guest disks stay off the root file system. Vagrant keeps its plugins under `VAGRANT_HOME` as well. A plugin installed into `~/.vagrant.d` is invisible to the make targets, so install it with the same value.

```bash
export VAGRANT_HOME=/data/rfl-vagrant/home
vagrant plugin install vagrant-vmware-desktop --plugin-version 3.0.5
vagrant plugin list
```

`make vagrant-up` refuses any plugin version other than `vagrant.vmware_desktop_plugin`. The Vagrantfile relies on two behaviors of 3.0.5, read from its source. Its disk capability grows the primary disk of a full clone and attaches the seed ISO. Its network action creates a host-only vmnet without DHCP for a private network with a static address.

**Not run on the lab host.** When everything above is in place, `make vagrant-check` prints `[ok]` for every line.

## 4. The box

HCP Vagrant, which hosts the public box registry, stopped taking new boxes on 2026-10-01 and decommissions everything on 2026-12-31 (`https://hashicorp.com/blog/hcp-vagrant-deprecation-important-dates-and-migration-guidance`). The lab uses no registry box.

```bash
make vagrant-box
```

The target checks the `vagrant` scope of the lock, downloads `lab.guest_image` into `$(VAGRANT_DATA)/downloads` unless it is there, and refuses the file unless it hashes to the pinned value. `qemu-img` converts it into a monolithic sparse VMDK on an LSI Logic adapter. The box is a gzip tar of `metadata.json`, `box.vmx` and `disk.vmdk`, written to `$(VAGRANT_DATA)/boxes/rfl-noble-20261017.box` with a `.sha256` file beside it. A second run keeps the box while its inputs hold. `make vagrant-up` adds it to Vagrant with that checksum.

It needs no VMware. It ran on the lab host on 2026-11-16 (`box-build-1.log`, `box-verify.log`, `box-rerun-and-break.log`).

- The build took 13 s and wrote a 626 MiB box.
- `qemu-img compare` found the VMDK identical to `lab.guest_image`.
- The disk-size parser of the plugin, run as Ruby on the VMDK, read 3758096384 bytes, so the plugin sees a 3.5 GiB disk and grows it.
- A second run reported the box up to date. A truncated image was refused.
- A second build from an empty data directory downloaded the image and took 27 s. Its box differs from the first in 8 bytes, the random CID `qemu-img` writes into the VMDK descriptor, and `qemu-img compare` found both disks identical (`box-build-fresh.log`, `box-two-builds.log`). So each build has its own checksum, and `vagrant box add` checks the `.sha256` of the build it adds.

A probe of the image chose it over Ubuntu's OVA build. The image boots kernel 6.8.0-147-generic, which equals `lab.guest_kernel`, so `make bootstrap` accepts the guests as it accepts the harness guests. Its initrd holds `mptspi`, `ahci`, `nvme`, `vmw_pvscsi`, `e1000e` and `vmxnet3`, so every VMware disk adapter and NIC works at boot. It ships `open-vm-tools`, and its cloud-init data source list starts with NoCloud, which reads the seed ISO.

| VMX choice | Why |
|---|---|
| `virtualHW.version = 19` | Workstation 16.2 hardware, which every newer release opens |
| `firmware = bios` | The image boots BIOS and UEFI. BIOS is the path the harness proved |
| `scsi0.virtualDev = lsilogic` | Matches the VMDK adapter type, and `mptspi` is in the initrd |
| `serial0` to `serial.log` | The image kernel writes its console to ttyS0, so each guest folder keeps its boot log |

The plugin sets every NIC to `e1000e` itself.

## 5. Bring up the lab

Run `make doctor` before the first `make vagrant-up`. Once Vagrant has created the host-only vmnets, the host holds `192.168.140.1` and `10.77.0.1`, and doctor reports them as overlaps with the address plan. This follows from what doctor compares and was not run.

```bash
make lab-down        # when QEMU guests of this host run
make vagrant-check
make vagrant-up
```

`make vagrant-up` runs these steps in order.

1. The boot check, which refuses while a `qemu-system` process runs or anything from sections 2 and 3 is missing.
2. `make vagrant-box`, then `vagrant box add` unless the same box bytes are already added.
3. The seeds under `.lab/vagrant`. An SSH key, one SSH host key per guest, a `known_hosts` file that names each guest, and one NoCloud seed ISO per guest. The seed is the harness seed with VMware MACs. It creates the user `ubuntu`, renames the lab NIC `lab0` and sets the management address. Seeds were written on the lab host on 2026-11-16, and a second run changed nothing.
4. `vagrant up`, which clones the box per guest, grows its disk, attaches its seed and boots it.
5. The override inventory. It waits for SSH and `cloud-init status --wait` on each guest, finds each NIC by MAC with `ip -j link show`, and writes `.lab/vagrant/inventory.yml`.

| Network | VMware network | DHCP | Guest address | Carries |
|---|---|---|---|---|
| NAT | vmnet8 | Yes | Lease of the VMware DHCP server | SSH from the host, the default route, package downloads |
| Management | Host-only, `192.168.140.0/24` | No | `k8s_node_ip` from the seed | Kubernetes, the API at `192.168.140.11:6443` |
| Lab | Host-only, `10.77.0.0/24` | No | None until `make bootstrap` sets `lab_rdma_ip` on `lab0` | RXE between the workers, Multus `net1` |

When a vmnet with one of these subnets already exists, the plugin reuses it as it is, DHCP included, so check `vmware-netcfg` first.

SSH goes to the NAT address on port 22. No host port listens for it. The plugin reads the address from the DHCP lease of the NAT MAC, because `vmrun getGuestIPAddress` can return the address of another NIC. The network role of `make bootstrap` requires the SSH session to arrive on the NIC that holds the default route. The override inventory names that NIC `lab_uplink_nic`, as the harness does. Each guest is reached with `HostKeyAlias=<guest>` against `.lab/vagrant/ssh/known_hosts`, so a new DHCP lease keeps host key checking on.

| Variable | Default | Holds |
|---|---|---|
| `VAGRANT_DATA` | `/data/rfl-vagrant` | `downloads/`, `boxes/`, `home/` (Vagrant's home), `dotfile/` (machine records), `machines/` (guest folders with disks and `serial.log`) |
| `VAGRANT_HOME` | `$(VAGRANT_DATA)/home` | Boxes and plugins |
| `VAGRANT_STATE` | `.lab/vagrant` | Keys, seeds, `inventory.yml`. Gitignored with the rest of `.lab/` |

Set `VAGRANT_DATA` on the make command line or in the environment to move the large files, and install the plugin into the matching `VAGRANT_HOME`. Each guest disk starts as a 1.75 GiB copy of the box disk (`box-verify.log`) and grows as the guest writes.

`make vagrant-status`, `make vagrant-ssh GUEST=<name>` and `make vagrant-inventory` work on running guests. Run Vagrant only through these targets. The Vagrantfile refuses to load without the environment they set.

**Not run on the lab host** beyond the seed step. On 2026-11-16 every target refused because Vagrant and VMware were missing, and `make vagrant-up` and `make vagrant-check` also because three QEMU guests ran (`refusals.log`).

## 6. M1 to M3 on the Vagrant guests

The hostNetwork RDMA test pods run the image `make rdma-tools-image` builds inside the Track B QEMU guest. Build it before `make vagrant-up`, then stop that guest with `LAB_PROFILE=ibsim make lab-down`.

```bash
O=LAB_OVERRIDE=.lab/vagrant/inventory.yml
make bootstrap "$O"
make rxe-up "$O"
make rxe-test "$O"
make rxe-test "$O" SCENARIO=vm-rxe-reboot
make k8s "$O"
make addons ENV=lab KUBECONFIG_FILE=.lab/kubeconfig
make k8s-test "$O"
make rdma-tools-load "$O"
make k8s-hostnet-test "$O"
make k8s-multus-test "$O"
```

The QEMU harness forwards the API to `127.0.0.1` and sets `LAB_API_URL` when its listing exists. `mk/vagrant.mk` clears `LAB_API_URL` whenever `LAB_OVERRIDE` is the Vagrant inventory, so the kubeconfig keeps `https://192.168.140.11:6443`, which the host reaches through its host-only adapter. Both labs write the admin kubeconfig to `.lab/kubeconfig`, so the last `make k8s` decides which cluster it names.

**Not run on the lab host.** M1 to M3 run here in phase 2, and their run ids will be recorded in this section.

## 7. Stop and clean up

```bash
make vagrant-down     # shut the guests down, keep their disks
make vagrant-wipe     # destroy the guests, their disks, seeds, known_hosts and inventory
```

`make vagrant-wipe` keeps the SSH client key, the box, Vagrant's home and the image download. Delete `$(VAGRANT_DATA)` by hand to remove those. **Not run on the lab host.**

## 8. Open on this path

The first VMware run decides each of these. It waits until the remaining milestones are done. Until then nothing on this path has booted a guest, and the QEMU harness stays the verified way to run the lab.

- Whether a VMware guest powers on beside `kvm.enable_virt_at_load=Y` (section 2.5).
- Whether `vmware-modconfig` builds vmmon and vmnet on kernel 7.0 (section 2.3).
- Whether Workstation opens the VMDK `qemu-img` wrote, and whether `vmware-vdiskmanager -x` grows it.
- Whether the host-only vmnet passes the extra MAC addresses of macvlan children. `make k8s-multus-test` and `tests/integration/qemu-lab/probe-m3.sh macvlan` show it. If it does not, check whether VMware refused the guest promiscuous mode. Older VMware documentation says a Linux host grants it only to a user who can write `/dev/vmnet<N>`. That is a guess from memory and was not checked here.
- Whether the lab vmnet forwards RoCEv2 UDP 4791 between the workers, and the management vmnet forwards Cilium VXLAN UDP 8472.
- The NIC names the guests give their NAT and management NICs, which the override inventory records.
