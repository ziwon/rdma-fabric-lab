import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from vagrant import box, plan

REPO_ROOT = Path(__file__).resolve().parents[2]
VAGRANTFILE = REPO_ROOT / "vagrant" / "Vagrantfile"
LOCK = yaml.safe_load((REPO_ROOT / "versions.lock.yaml").read_text())
# Stands in for Vagrant: every call on config is recorded with its arguments, and a block gets a
# recorder whose path names the call and its arguments, as config.vm.define "cp1" do |guest| does.
RECORDER = r"""
require "json"
class Recorder
  def initialize(path, log)
    @path = path
    @log = log
  end

  def method_missing(name, *args, **kw, &blk)
    @log << { "call" => (@path + [name.to_s]).join("."), "args" => args, "kw" => kw }
    child = Recorder.new(@path + [name.to_s] + (blk ? args.map(&:to_s) : []), @log)
    blk&.call(child)
    child
  end

  def respond_to_missing?(*)
    true
  end
end

module Vagrant
  def self.configure(_version)
    log = []
    yield Recorder.new(["config"], log)
    puts JSON.generate(log)
  end
end

load ARGV.fetch(0)
"""
needs_ruby = pytest.mark.skipif(shutil.which("ruby") is None, reason="ruby is not installed")


@pytest.fixture(scope="module")
def recorded(tmp_path_factory) -> list[dict]:
    state = tmp_path_factory.mktemp("state")
    script = state / "recorder.rb"
    script.write_text(RECORDER)
    out = subprocess.run(["ruby", str(script), str(VAGRANTFILE)], capture_output=True, text=True, check=True,
                         env={"PATH": "/usr/bin:/bin", "RFL_VAGRANT_STATE": str(state)})
    return json.loads(out.stdout), str(state)


def calls(log: list[dict], call: str) -> list[dict]:
    return [entry for entry in log if entry["call"] == call]


def test_the_vagrantfile_holds_no_address_size_or_mac_of_its_own():
    text = VAGRANTFILE.read_text()
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", text)
    assert not re.search(r"\b(4096|6144|30|40)\b", text)
    assert not re.search(r"([0-9a-fA-F]{2}:){5}", text)


@needs_ruby
def test_each_machine_gets_the_sizes_macs_and_addresses_of_the_plan(recorded):
    log, state = recorded
    assert calls(log, "config.vm.box=")[0]["args"] == [box.box_name(LOCK)]
    assert calls(log, "config.ssh.private_key_path=")[0]["args"] == [f"{state}/ssh/id_ed25519"]
    profile = plan.harness().parse(plan.PROFILE.read_text())
    names = [entry["args"][0] for entry in calls(log, "config.vm.define")]
    assert names == [g.name for g in profile.guests]
    for guest in profile.guests:
        provider = f"config.vm.define.{guest.name}.vm.provider.vmware_desktop"
        sizes = (calls(log, f"{provider}.cpus=")[0]["args"], calls(log, f"{provider}.memory=")[0]["args"],
                 calls(log, f"config.vm.define.{guest.name}.vm.disk")[0]["kw"]["size"])
        assert sizes == ([guest.vcpus], [guest.mem_mib], f"{guest.disk_gib}GB")
    worker = "config.vm.define.worker1"
    vmware = f"{worker}.vm.provider.vmware_desktop"
    assert calls(log, f"{vmware}.base_mac=")[0]["args"] == ["00:50:56:0C:00:12"]
    assert calls(log, f"{vmware}.linked_clone=")[0]["args"] == [False]
    networks = calls(log, f"{worker}.vm.network")
    assert [(n["args"], n["kw"]["ip"], n["kw"]["mac"], n["kw"]["auto_config"]) for n in networks] == [
        (["private_network"], "10.77.0.12", "0050560d0012", False), (["private_network"], "192.168.140.12", "0050562d0012", False)]
    disks = {d["args"][0]: d["kw"] for d in calls(log, f"{worker}.vm.disk")}
    assert disks["disk"] == {"name": "root", "size": "40GB", "primary": True}
    assert disks["dvd"] == {"name": "seed", "file": f"{state}/guests/worker1/seed.iso"}


@needs_ruby
def test_ssh_skips_port_forwards_and_vagrant_keeps_its_hands_off_the_guest_network(recorded):
    log, _ = recorded
    forwards = calls(log, "config.vm.network")
    assert forwards == [{"call": "config.vm.network", "args": ["forwarded_port"],
                         "kw": {"id": "ssh", "guest": 22, "host": 2222, "disabled": True}}]
    assert calls(log, "config.ssh.insert_key=")[0]["args"] == [False]
    assert calls(log, "config.vm.define.cp1.vm.provider.vmware_desktop.ssh_info_public=")[0]["args"] == [True]
    assert calls(log, "config.vm.define.cp1.vm.provider.vmware_desktop.enable_vmrun_ip_lookup=")[0]["args"] == [False]
    assert all(n["kw"]["auto_config"] is False for n in calls(log, "config.vm.define.cp1.vm.network"))


@needs_ruby
def test_the_vagrantfile_refuses_to_run_outside_the_make_targets(tmp_path):
    script = tmp_path / "recorder.rb"
    script.write_text(RECORDER)
    out = subprocess.run(["ruby", str(script), str(VAGRANTFILE)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert out.returncode != 0 and "RFL_VAGRANT_STATE is unset" in out.stderr
