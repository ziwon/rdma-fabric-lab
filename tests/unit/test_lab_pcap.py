from pathlib import Path

import pytest

from run import pcap

PCAPS = Path(__file__).resolve().parents[1] / "fixtures" / "a-soft-roce" / "pcap"


def test_lab_capture_holds_rocev2_between_the_rdma_addresses():
    summary = pcap.summarize((PCAPS / "lab-first-64-frames.pcap").read_bytes())
    assert summary.frames == 64
    assert summary.rocev2_frames > 0
    assert set(summary.rocev2_flows) <= {"10.77.0.12>10.77.0.13", "10.77.0.13>10.77.0.12"}
    assert summary.ssh_syn_frames == 0
    assert summary.first_ts <= summary.last_ts


def test_management_capture_has_ssh_setups_and_no_rocev2():
    summary = pcap.summarize((PCAPS / "mgmt.pcap").read_bytes())
    assert summary.rocev2_frames == 0
    assert summary.ssh_syn_frames > 0
    assert summary.frames >= summary.ssh_syn_frames


def test_capture_cut_mid_record_counts_only_whole_records():
    data = (PCAPS / "lab-first-64-frames.pcap").read_bytes()
    assert pcap.summarize(data[:-10]).frames == 63


@pytest.mark.parametrize("data", [b"", b"\x0a\x0d\x0d\x0a" + b"\x00" * 40, b"not a capture at all, just text"])
def test_non_pcap_input_is_refused(data):
    with pytest.raises(pcap.PcapError):
        pcap.summarize(data)


def test_marker_pings_on_the_management_nic_are_counted_and_no_rocev2_is_there():
    summary = pcap.summarize((PCAPS / "mgmt-markers.pcap").read_bytes())
    assert summary.icmp_echo_frames > 0 and summary.rocev2_frames == 0 and summary.ssh_syn_frames == 0


def test_without_a_tunnel_port_no_frame_counts_as_vxlan():
    summary = pcap.summarize((PCAPS.parent / "k8s/worker1-mgmt-vxlan.pcap").read_bytes())
    assert summary.tunnel_frames == 0 and summary.watch_net_frames == 0
