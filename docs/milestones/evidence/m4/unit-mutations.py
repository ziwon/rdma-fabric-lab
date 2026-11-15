"""Apply one mutation at a time to a scratch copy of the repository and run the ibsim unit tests."""
import shutil
import subprocess
import sys
from pathlib import Path

repo, work, py = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
MUTATIONS = [
    ("routes direction", "scripts/ibsim/verdict.py", "hops[port.node].get(peer.node) != distance - 1", "False"),
    ("duplicate LIDs", "scripts/ibsim/verdict.py", "if len(ports) > 1]", "if False]"),
    ("asymmetric link", "scripts/ibsim/topology.py", "if said.get(there) != here:", "if False:"),
    ("CapAdd", "scripts/ibsim/run.py", 'if host.get("CapAdd"):', "if False:"),
    ("guest marker", "tracks/b-ibsim/scripts/guest.sh", "| ls) require_guest ;;", "| ls) : ;;"),
    ("qemu allowlist", "tests/integration/qemu-lab/guests.py", "unknown = sorted(set(flags) - QEMU_FLAGS)", "unknown = []"),
    ("link peer", "scripts/ibsim/observe.py", 'remote = Port(found["remote"], int(found["rport"])) if found["rport"] else None', "remote = None"),
    ("silent LIDs", "scripts/ibsim/verdict.py", "if answers.get(lid) is not None]", "if False]"),
]
for name, rel, old, new in MUTATIONS:
    if work.exists():
        shutil.rmtree(work)
    for top in ("scripts", "tests", "tracks", "sites", "images", "versions.lock.yaml"):
        src = repo / top
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, work / top, **({"ignore": shutil.ignore_patterns("__pycache__")} if src.is_dir() else {}))
    target = work / rel
    text = target.read_text()
    assert text.count(old) == 1, (name, old)
    target.write_text(text.replace(old, new))
    done = subprocess.run([py, "-m", "pytest", "-q", "-p", "no:cacheprovider", *sorted(str(p) for p in (work / "tests/unit").glob("test_ibsim_*.py"))],
                          capture_output=True, text=True, cwd=work)
    failed = [line.split(" ")[1].split("::")[-1] for line in done.stdout.splitlines() if line.startswith("FAILED")]
    summary = done.stdout.strip().splitlines()[-1]
    print(f"{name}: pytest rc={done.returncode}; {summary}")
    for test in failed[:4]:
        print(f"    {test}")
shutil.rmtree(work)
