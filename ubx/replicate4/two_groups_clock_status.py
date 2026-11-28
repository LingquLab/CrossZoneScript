"""Read bounded evidence from the EP32 clock calibration."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shlex

import benchmark

ROOT = "/root/ubx-replicate4-rdma-two-groups-bs32-af8c365"
HOSTS = ("90.90.93.83", "90.90.93.86")
PROBE = """
from pathlib import Path
import json
import sys

root = Path(sys.argv[1])
output = root / sys.argv[2]
directory = output / "clock-calibration"
models = sorted(path.stem for path in directory.glob("*.model"))
logs = {}
for path in sorted(directory.glob("*.log")):
    if path.stem not in models:
        with path.open("rb") as log:
            log.seek(max(0, path.stat().st_size - 2048))
            logs[path.stem] = log.read().decode(errors="replace").splitlines()[-4:]
print(json.dumps(dict(models=models, logs=logs, workers=len(list(output.glob("rank*/worker.log"))))))
"""


def inspect(host, run_name):
    command = shlex.join(["python3", "-", ROOT, run_name])
    data = json.loads(benchmark.ssh(host, command, payload=PROBE.encode(), timeout=25))
    errors = Counter()
    for lines in data["logs"].values():
        for line in lines:
            if "@" not in line:
                message = re.sub(r"^\[.*?\] \[\w+\] ", "", line.strip())
                if message:
                    errors[message[:190]] += 1
    return dict(host=host, local_models=sum(x.startswith("local") for x in data["models"]),
                bridge="bridge" in data["models"], missing_logs=sorted(data["logs"]),
                errors=errors.most_common(4), workers=data["workers"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    options = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    run_name = options["run_name"]
    assert re.fullmatch(r"full-bs32-f50-[a-z0-9-]+", run_name)
    with ThreadPoolExecutor(2) as pool:
        print(json.dumps(list(pool.map(lambda host: inspect(host, run_name), HOSTS)), separators=(",", ":")))
