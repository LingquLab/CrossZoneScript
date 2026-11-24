"""Build the pinned 64P Full candidate and run three functional SST cases."""
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
assert set(request) == {"revision"}
revision = request["revision"]
assert re.fullmatch("[0-9a-f]{40}", revision)
scripts = Path(__file__).resolve().parent

with tempfile.TemporaryDirectory(prefix="urma-full-groups-") as temp:
    inputs = Path(temp) / "args.json"
    inputs.write_text(json.dumps(dict(revision=revision)), encoding="utf-8")
    build = subprocess.run([sys.executable, str(scripts / "build.py"), "--args-file", str(inputs)],
                           capture_output=True, text=True, timeout=640)
    candidates = []
    for line in build.stdout.splitlines():
        item = json.loads(line)
        print(json.dumps(item), flush=True)
        if item.get("stage") == "compile" and item.get("passed") and item.get("revision") == revision:
            candidates.append(item["run_dir"])
    if build.returncode or len(candidates) != 1:
        raise SystemExit(build.returncode or 1)
    inputs.write_text(json.dumps(dict(build=candidates[0], expected_revision=revision,
                                     kind="functional", size="512kib-per-rank", timeout=1500,
                                     deadline=110000, schedules=["syncall", "clock", "nosync"])),
                      encoding="utf-8")
    raise SystemExit(subprocess.run([sys.executable, str(scripts / "run_cases.py"),
                                     "--args-file", str(inputs)], timeout=5200).returncode)
