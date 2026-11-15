from pathlib import Path

import pytest

from run import parse

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "a-soft-roce"


def fixture(rel: str) -> str:
    return (FIXTURES / rel).read_text()


@pytest.mark.parametrize(
    ("rel", "test"),
    [
        ("perftest/ib_read_bw.client.stdout", "RDMA_Read BW Test"),
        ("perftest/ib_read_bw.server.stdout", "RDMA_Read BW Test"),
        ("perftest/ib_write_bw.client.stdout", "RDMA_Write BW Test"),
        ("perftest/ib_send_bw.client.stdout", "Send BW Test"),
        ("perftest/ib_send_bw.server.stdout", "Send BW Test"),
    ],
)
def test_perftest_result_row_of_real_runs(rel, test):
    result = parse.perftest_bw(fixture(rel))
    assert result.test == test
    assert (result.bytes, result.iterations) == (4096, 1000)
    assert result.bw_unit == "Gb/sec"
    assert result.device == "rxe0"
    assert result.connection == "rdma_cm"
    assert result.gid_index is not None
    assert result.bw_average > 0


def test_perftest_client_values():
    result = parse.perftest_bw(fixture("perftest/ib_read_bw.client.stdout"))
    assert (result.bw_peak, result.bw_average, result.msg_rate_mpps) == (2.08, 2.08, 0.063601)
    assert result.gid_index == 1


@pytest.mark.parametrize(
    "rel", ["perftest/ib_read_bw.client-port-down.stderr", "perftest/ib_read_bw.client-addr-error.stdout"]
)
def test_failed_perftest_has_no_result(rel):
    with pytest.raises(parse.ParseError):
        parse.perftest_bw(fixture(rel))


def test_perftest_header_without_row_has_no_result():
    text = fixture("perftest/ib_read_bw.client.stdout")
    cut = text[: text.index("#bytes")] + text[text.index("#bytes") :].splitlines()[0] + "\n"
    with pytest.raises(parse.ParseError):
        parse.perftest_bw(cut)


def test_rping_prints_every_iteration_on_both_ends():
    assert parse.rping_iterations(fixture("rping/client.stdout")) == list(range(10))
    assert parse.rping_iterations(fixture("rping/server.stdout")) == list(range(10))


def test_rdma_links():
    active = parse.rdma_links(fixture("rdma/link-active.json"))
    down = parse.rdma_links(fixture("rdma/link-down.json"))
    assert [(link["ifname"], link["netdev"], link["state"]) for link in active] == [("rxe0", "enp0s4", "ACTIVE")]
    assert down[0]["state"] == "DOWN"


def test_listener_matches_tool_and_port():
    rping = fixture("rdma/cm_id-rping-listen.json")
    perftest = fixture("rdma/cm_id-ib_read_bw-listen.json")
    assert parse.cm_listening(rping, "rping", 7174)
    assert not parse.cm_listening(rping, "rping", 7175)
    assert not parse.cm_listening(rping, "ib_read_bw", 7174)
    assert parse.cm_listening(perftest, "ib_read_bw", 18515)
    assert not parse.cm_listening("", "rping", 7174)
    assert not parse.cm_listening("not json", "rping", 7174)


def test_recap_of_a_first_run_and_a_rerun():
    first = parse.ansible_recap(fixture("ansible/bootstrap-first-run.recap.txt"))
    rerun = parse.ansible_recap(fixture("ansible/bootstrap-rerun.recap.txt"))
    assert {h: c["changed"] for h, c in first.items()} == {"worker1": 8, "worker2": 8}
    assert {h: c["changed"] for h, c in rerun.items()} == {"worker1": 0, "worker2": 0}
    assert all(c["failed"] == 0 and c["unreachable"] == 0 for c in rerun.values())


def test_output_without_recap_is_refused():
    with pytest.raises(parse.ParseError):
        parse.ansible_recap("ERROR! the playbook could not be found")


@pytest.mark.parametrize(
    ("rc", "rel", "expected"),
    [
        (0, "rping/client.stdout", None),
        (255, "rping/client-etimedout.stderr", "timeout"),
        (255, "rping/client-enodev.stderr", "command-failed"),
        (1, "perftest/ib_read_bw.client-port-down.stderr", "command-failed"),
        (124, "perftest/ib_read_bw.client.stdout", "timeout"),
        (137, "perftest/ib_read_bw.client.stdout", "timeout"),
        (None, "perftest/ib_read_bw.client.stdout", "timeout"),
    ],
)
def test_failure_class(rc, rel, expected):
    assert parse.failure_class(rc, fixture(rel)) == expected
