"""Inspect the timed-out 64P build and resume the unfinished validation."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
request = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
assert set(request) == {"revision", "prior_build"}
revision, build_dir = request["revision"], request["prior_build"]
assert re.fullmatch("[0-9a-f]{40}", revision)
assert re.fullmatch(r"/opt/urma64p-full\.[A-Za-z0-9]{6}", build_dir)
scripts = Path(__file__).resolve().parent

source = f"revision = {revision!r}\nbase = {build_dir!r}\n" + r"""
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(base)
assert root.is_dir()
containers = subprocess.check_output(["docker", "ps", "--filter", "volume=" + base, "-q"],
                                     text=True, timeout=12).splitlines()
expected = {f"{mode}/{kind}/tilexr_alltoall_urma_jetty_peers_full_kernel.o"
            for mode in ("syncall", "clock", "nosync")
            for kind in ("functional", "performance")}
record = root / "objects.json"
verified = False
if record.is_file():
    data = json.loads(record.read_text())
    verified = (data.get("revision") == revision and data.get("passed") is True and
                set(data.get("objects", {})) == expected and
                all((root / name).is_file() and
                    hashlib.sha256((root / name).read_bytes()).hexdigest() == data["objects"][name]
                    for name in expected))
print(json.dumps(dict(prior_build=base, active_containers=len(containers), verified=verified,
                      compiler_log=(root / "build.log").is_file(),
                      existing_objects=sum((root / name).is_file() for name in expected))))
"""
ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
       "-o", "IdentitiesOnly=yes", "-i", str(Path.home() / ".ssh/id_ed25519_server"),
       "root@141.61.94.30", "python3 -u -"]
inspection = subprocess.run(ssh, input=source.encode(), capture_output=True, timeout=35)
if inspection.returncode:
    print(json.dumps(dict(stage="inspect", ssh_exit=inspection.returncode)), flush=True)
    raise SystemExit(1)
evidence = json.loads(inspection.stdout)
print(json.dumps(evidence), flush=True)
assert evidence["active_containers"] == 0, "prior build still active"

with tempfile.TemporaryDirectory(prefix="urma-groups-recovery-") as temp:
    args = Path(temp) / "args.json"
    if evidence["verified"]:
        entry = "run_cases.py"
        request = dict(build=build_dir, expected_revision=revision, kind="functional",
                       size="512kib-per-rank", timeout=1500, deadline=110000,
                       schedules=["syncall", "clock", "nosync"])
    else:
        entry = "build_run_groups.py"
        request = dict(revision=revision)
    args.write_text(json.dumps(request), encoding="utf-8")
    result = subprocess.run([sys.executable, str(scripts / entry), "--args-file", str(args)],
                            timeout=5950)
raise SystemExit(result.returncode)
