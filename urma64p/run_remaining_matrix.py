"""Run the remaining Full URMA performance cases, one SST container at a time."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
runner = Path(__file__).resolve().with_name("run_cases.py")
jobs = (
    ("512kib-per-rank", 900),
    ("4mib-per-rank", 2400),
    ("16mib-per-rank", 6500),
)
failed = False
with tempfile.TemporaryDirectory(prefix="urma64p-matrix-") as temp:
    config = Path(temp) / "case.json"
    for size, timeout in jobs:
        config.write_text(json.dumps(dict(
            build=args["build"], kind="performance", size=size, timeout=timeout,
            deadline=0, auto_clock=True, schedules=["syncall", "nosync", "clock"],
        )), encoding="utf-8")
        result = subprocess.run([sys.executable, str(runner), "--args-file", str(config)])
        print(json.dumps({"stage": "size_finished", "size": size, "exit": result.returncode}), flush=True)
        failed |= result.returncode != 0
raise SystemExit(1 if failed else 0)
