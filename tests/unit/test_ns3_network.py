"""The mapping from the scenario's human units to the integers rfl-star applies, and the FCT bound."""

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from ns3 import network

REPO = Path(__file__).resolve().parents[2]
SCENARIOS = yaml.safe_load((REPO / "tracks/c-ns3/config/scenarios.yaml").read_text())["scenarios"]
SINGLE = SCENARIOS["c-02-single-flow"]["network"]


@pytest.mark.parametrize("text, bps", [("100Gbps", 100 * 10**9), ("25Gbps", 25 * 10**9), ("400Mbps", 4 * 10**8), ("8bps", 8)])
def test_rates_use_the_si_units_of_ns3_data_rate(text, bps):
    assert network.rate_bps(text) == bps


@pytest.mark.parametrize("text, ns", [("1us", 1000), ("0ns", 0), ("10ms", 10**7), ("2s", 2 * 10**9)])
def test_times_become_nanoseconds(text, ns):
    assert network.time_ns(text) == ns


@pytest.mark.parametrize("text", ["1.5us", "100GBps", "100 Gbps", "1GiBps", "", "us"])
def test_a_fraction_or_an_unknown_unit_is_refused(text):
    with pytest.raises(network.NetworkError):
        network.quantity(text, {**network.RATE_UNITS, **network.TIME_UNITS}, "value")


def test_the_single_flow_scenario_maps_to_the_model_units():
    net = network.star(SINGLE)
    assert [(link.host, link.bps, link.delay_ns) for link in net.links] == [
        ("sender", 100_000_000_000, 1000), ("receiver", 100_000_000_000, 1000)]
    assert (net.kmin_kb, net.kmax_kb) == (400, 1600)
    assert net.pause_time_us == 5 and net.stop_ns == 10_000_000 and net.cc_mode == 1
    assert net.flows[0] == network.Flow("sender", "receiver", 1_000_000, 0, 3)
    assert net.switch == 2 and net.node("receiver") == 1


def test_an_ecn_threshold_that_is_not_whole_model_kilobytes_is_refused():
    with pytest.raises(network.NetworkError, match="multiple of 1000"):
        network.star({**SINGLE, "ecn_kmin_bytes": 409600})


def test_a_pause_time_below_one_microsecond_is_refused():
    with pytest.raises(network.NetworkError, match="whole microseconds"):
        network.star({**SINGLE, "pfc_pause_time": "500ns"})


def test_rendering_is_deterministic_and_states_every_setting_once():
    net = network.star(SINGLE)
    text = network.render(net, "header")
    assert text == network.render(network.star(SINGLE), "header")
    keys = [line.split()[0] for line in text.splitlines() if line and not line.startswith("#")]
    settings = [k for k in keys if k not in ("host", "switch", "link", "flow")]
    assert len(settings) == len(set(settings)) == 29
    assert "link 0 2 100000000000 1000" in text and "link 1 2 100000000000 1000" in text
    assert "flow 0 1 1000000 0 3" in text and "kmin_kb 400" in text and "error_rate 0.0" in text


def test_a_changed_rate_changes_only_that_host_link():
    net = network.with_link_rate(network.star(SINGLE), "receiver", 0.25)
    assert [link.bps for link in net.links] == [100_000_000_000, 25_000_000_000]


def test_transmit_time_truncates_to_whole_nanoseconds_as_the_model_does():
    assert network.tx_ns(1052, 100 * 10**9) == 84
    assert network.tx_ns(1000, 100 * 10**9) == 80
    assert network.tx_ns(60, 100 * 10**9) == 4


def test_the_wire_sizes_follow_the_fork_headers():
    assert (network.DATA_HEADER_BYTES, network.ACK_BYTES) == (52, 60)


def test_payloads_split_at_the_mtu_with_a_short_last_packet():
    assert network.packet_payloads(2500, 1000) == [1000, 1000, 500]
    assert network.packet_payloads(2000, 1000) == [1000, 1000]


def test_the_fct_bound_and_its_decomposition_for_the_single_flow():
    net = network.star(SINGLE)
    bound = network.fct_bound(net, net.flows[0])
    assert bound.payload_serialization_ns == 80_000.0 and bound.propagation_ns == 2000
    assert bound.bound_ns == 82_000.0
    assert bound.header_ns == 1000 * 84 - 80_000
    assert bound.store_and_forward_ns == 84
    assert bound.ack_return_ns == 4 + 4 + 2000
    assert bound.expected_ns == 1000 * 84 + 2000 + 84 + 2008 == 88_092


def test_the_bound_uses_the_slower_link_as_the_bottleneck():
    net = network.with_link_rate(network.star(SINGLE), "receiver", 0.5)
    bound = network.fct_bound(net, net.flows[0])
    assert bound.payload_serialization_ns == 160_000.0


def test_a_flow_between_unknown_hosts_is_refused():
    params = {**SINGLE, "flows": [{"src": "sender", "dst": "nobody", "payload_bytes": 1, "start": "0ns", "priority": 3}]}
    with pytest.raises(network.NetworkError):
        network.star(params)


def test_priority_zero_is_refused_because_the_switch_reserves_queue_zero_for_control_frames():
    params = {**SINGLE, "flows": [{**SINGLE["flows"][0], "priority": 0}]}
    with pytest.raises(network.NetworkError, match="priority 1 to 7"):
        network.star(params)


def test_replace_keeps_the_star_frozen():
    net = network.star(SINGLE)
    assert replace(net, error_rate=0.01).error_rate == 0.01 and net.error_rate == 0.0
