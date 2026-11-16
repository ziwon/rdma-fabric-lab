"""Parsers of what the Track C image writes, on files captured from real runs (tests/fixtures/c-ns3/README.md)."""

from pathlib import Path

import pytest

from ns3 import results

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "c-ns3"


def test_gnu_time_gives_wall_seconds_and_max_rss():
    figures = results.gnu_time((FIXTURES / "probe" / "simai-run1.time").read_text())
    assert figures == {"wall_s": pytest.approx(4.08), "max_rss_kib": 2575872.0, "exit_status": 0.0}


def test_gnu_time_without_the_rss_line_is_refused():
    text = "\n".join(line for line in (FIXTURES / "probe" / "simai-run1.time").read_text().splitlines()
                     if "Maximum resident" not in line)
    with pytest.raises(results.ResultError):
        results.gnu_time(text)


def test_the_total_time_of_the_simai_example_is_read_from_its_last_line():
    text = (FIXTURES / "probe" / "ncclFlowModel_EndToEnd.csv").read_text()
    assert results.last_number_after(text, "total time,") == 389404.03
    assert results.last_number_after(text, "no such prefix") is None


def test_rows_refuse_a_header_with_other_columns():
    with pytest.raises(results.ResultError, match="header"):
        results.rows("a\tb\n1\t2\n", ("a", "c"))
    with pytest.raises(results.ResultError, match="cells"):
        results.rows("a\tb\n1\n", ("a", "b"))
