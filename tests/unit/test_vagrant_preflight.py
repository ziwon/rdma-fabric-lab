import dataclasses

from vagrant import preflight

PINS = {"vagrant.vagrant": "2.4.9", "vagrant.vmware_desktop_plugin": "3.0.5"}
READY = preflight.Facts(qemu=(), vagrant="2.4.9", plugin="3.0.5", utility_installed=True, utility_listening=True, vmrun=True,
                        modules=frozenset({"vmmon", "vmnet", "kvm_intel", "kvm"}), kvm_virt_at_load=False, vagrant_home="/data/home")

# Built from the format strings of Vagrant 2.4.9: lib/vagrant/ui.rb MachineReadable#machine,
# plugins/commands/plugin/action/list_plugins.rb and plugins/commands/box/command/list.rb.
PLUGIN_LIST = """1763270000,,ui,info,vagrant-vmware-desktop (3.0.5%!(VAGRANT_COMMA) global)
1763270000,,plugin-name,vagrant-vmware-desktop
1763270000,vagrant-vmware-desktop,plugin-version,3.0.5%!(VAGRANT_COMMA) global
"""
BOX_LIST = """1763270000,,box-name,rfl-noble-20261017
1763270000,,box-provider,vmware_desktop
1763270000,,box-version,0
1763270000,,box-architecture,amd64
1763270000,,box-name,other
1763270000,,box-provider,virtualbox
"""


def refused(facts, need="boot"):
    return {name: problem for name, problem in preflight.report(facts, need, PINS) if problem}


def _proc(tmp_path, processes):
    for pid, argv, comm in processes:
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (d / "comm").write_text(comm + "\n")
    (tmp_path / "self").mkdir()
    return tmp_path


def test_qemu_processes_match_argv0_even_when_the_harness_renamed_comm(tmp_path):
    proc = _proc(tmp_path, [(1614653, ["qemu-system-x86_64", "-name", "cp1,process=rfl-lab-cp1"], "rfl-lab-cp1"),
                            (2000, ["/usr/bin/qemu-system-aarch64", "-M", "virt"], "qemu-system-aar"),
                            (2001, ["/usr/bin/zsh", "-c", "qemu-system-x86_64 --version"], "zsh"),
                            (2002, ["qemu-img", "info", "disk.qcow2"], "qemu-img")])
    assert preflight.qemu_processes(proc) == [(2000, "/usr/bin/qemu-system-aarch64"), (1614653, "qemu-system-x86_64")]


def test_vagrant_version_reads_the_version_line():
    assert preflight.vagrant_version("Vagrant 2.4.9\n") == "2.4.9"
    assert preflight.vagrant_version("") is None


def test_plugin_version_undoes_the_comma_escape():
    assert preflight.plugin_version(PLUGIN_LIST) == "3.0.5"
    assert preflight.plugin_version(PLUGIN_LIST, "vagrant-libvirt") is None


def test_boxes_pairs_each_name_with_its_provider():
    assert preflight.boxes(BOX_LIST) == {("rfl-noble-20261017", "vmware_desktop"), ("other", "virtualbox")}


def test_loaded_modules_takes_the_first_field_of_proc_modules():
    assert preflight.loaded_modules("vmmon 159744 0 - Live 0x0\nkvm_intel 1 0 - Live 0x0\n") == {"vmmon", "kvm_intel"}


def test_a_ready_host_passes_every_check():
    assert refused(READY) == {}
    assert refused(READY, "provider") == {}


def test_boot_refuses_while_a_qemu_guest_runs_and_names_it():
    found = refused(dataclasses.replace(READY, qemu=((1614653, "qemu-system-x86_64"),)))
    assert list(found) == ["no-qemu"]
    assert "1614653 (qemu-system-x86_64)" in found["no-qemu"] and "holds VT-x" in found["no-qemu"]


def test_only_the_boot_target_looks_at_qemu():
    assert refused(dataclasses.replace(READY, qemu=((1, "qemu-system-x86_64"),)), "provider") == {}


def test_without_vagrant_the_plugin_is_reported_unchecked_not_missing():
    found = refused(dataclasses.replace(READY, vagrant=None, plugin=None))
    assert "Vagrant is not installed" in found["vagrant"]
    assert "not checked, because Vagrant does not run" in found["plugin"]


def test_a_missing_plugin_names_the_vagrant_home_to_install_it_into():
    found = refused(dataclasses.replace(READY, plugin=None))
    assert list(found) == ["plugin"] and "VAGRANT_HOME=/data/home" in found["plugin"] and "--plugin-version 3.0.5" in found["plugin"]


def test_versions_other_than_the_lock_are_refused():
    found = refused(dataclasses.replace(READY, vagrant="2.4.10", plugin="3.0.6"))
    assert "pins vagrant.vagrant 2.4.9" in found["vagrant"] and "pins 3.0.5" in found["plugin"]


def test_the_utility_must_be_installed_and_answer():
    assert "not installed" in refused(dataclasses.replace(READY, utility_installed=False, utility_listening=False))["utility"]
    assert "does not answer on 127.0.0.1:9922" in refused(dataclasses.replace(READY, utility_listening=False))["utility"]


def test_vmware_needs_vmrun_and_both_kernel_modules():
    assert "vmrun is not on PATH" in refused(dataclasses.replace(READY, vmrun=False))["vmware"]
    assert "vmnet are not loaded" in refused(dataclasses.replace(READY, modules=frozenset({"vmmon"})))["vmware"]


def test_kvm_enabling_virtualization_at_load_warns_the_boot_target_only():
    held = dataclasses.replace(READY, kvm_virt_at_load=True)
    assert len(preflight.warnings(held, "boot")) == 1 and "enable_virt_at_load=0" in preflight.warnings(held, "boot")[0]
    assert preflight.warnings(held, "provider") == []
    assert preflight.warnings(READY, "boot") == []
    assert preflight.warnings(dataclasses.replace(held, modules=frozenset({"vmmon", "vmnet"})), "boot") == []
