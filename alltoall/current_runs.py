"""Read the state of this session's Memory and LR8 SST runs on the approved host."""

import argparse
import json
from pathlib import Path
import subprocess


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--args-file", required=True, type=Path)
    domain = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))["domain"]
    assert domain in {"memory", "lr8"}
    source = Path(__file__).with_name("current_runs_remote.py").read_bytes()
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", "green-sst-30",
                             "python3 -B - " + domain], input=source, capture_output=True, timeout=35)
    if result.returncode:
        print(json.dumps(dict(ssh_exit=result.returncode, denied=b"Permission denied" in result.stderr)))
    else:
        print(result.stdout.decode("utf-8").strip())
