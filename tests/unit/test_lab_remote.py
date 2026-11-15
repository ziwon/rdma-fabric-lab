import os
import subprocess
import time

import pytest

from run import remote

RUN_ID = "20261115T010203Z"


def launch(tmp_path, name, command, timeout_s):
    line = remote.launch_command(RUN_ID, name, command, timeout_s, root=str(tmp_path))
    start = time.monotonic()
    subprocess.run(["bash", "-c", line], check=True, timeout=10, capture_output=True)
    return time.monotonic() - start, tmp_path / RUN_ID


def wait_for(path, seconds=10):
    deadline = time.monotonic() + seconds
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    return path.exists()


def test_launch_returns_while_the_process_still_runs(tmp_path):
    elapsed, run_dir = launch(tmp_path, "server", "sleep 3", 30)
    assert elapsed < 2
    assert wait_for(run_dir / "server.pid")
    assert not (run_dir / "server.rc").exists()
    assert wait_for(run_dir / "server.rc")
    assert (run_dir / "server.rc").read_text().strip() == "0"


def test_launch_bounds_the_process_with_timeout(tmp_path):
    _, run_dir = launch(tmp_path, "client", "sleep 30", 1)
    assert wait_for(run_dir / "client.rc", 15)
    assert (run_dir / "client.rc").read_text().strip() == "124"


def test_launch_keeps_stdout_stderr_and_exit_code_apart(tmp_path):
    _, run_dir = launch(tmp_path, "tool", "sh -c 'echo out; echo err >&2; exit 3'", 10)
    assert wait_for(run_dir / "tool.rc")
    assert (run_dir / "tool.out").read_text() == "out\n"
    assert (run_dir / "tool.err").read_text() == "err\n"
    assert (run_dir / "tool.rc").read_text().strip() == "3"


def test_run_marker_finds_this_run_only(tmp_path):
    _, run_dir = launch(tmp_path, "server", "sleep 20", 30)
    assert wait_for(run_dir / "server.pid")
    other = subprocess.Popen(["sleep", "20"], env={**os.environ, "RFL_RUN": "20261115T999999Z"})
    try:
        out = subprocess.run(["sh", "-c", remote.find_run_processes_command(RUN_ID)], capture_output=True, text=True)
        found = remote.parse_process_list(out.stdout)
        pids = {pid for pid, _ in found}
        assert int((run_dir / "server.pid").read_text()) in pids
        assert other.pid not in pids
        assert any("sleep 20" in cmd for _, cmd in found)
    finally:
        other.kill()
        for pid, _ in found:
            os.kill(pid, 9)


@pytest.mark.parametrize("bad", ["../etc", "20261115T010203Z/../../x", "latest", ""])
def test_run_dir_takes_only_a_run_id(bad):
    with pytest.raises(ValueError):
        remote.guest_run_dir(bad)


def test_process_list_skips_noise():
    assert remote.parse_process_list("12 sleep 20\n\nbogus line\n 7 rping -s \n") == [(12, "sleep 20"), (7, "rping -s")]
