"""Read an existing Memory SST run without launching a container."""

import argparse
import json
from pathlib import Path
import subprocess


BINDS = {
    "baseline": "baseline.T0P9snTI",
    "candidate": "candidate.sT0Wi2Qj",
    "diagnostic-baseline": "diagnostic-baseline.ZBW4PP58",
    "diagnostic-candidate": "diagnostic-candidate.txzclMel",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--args-file", required=True, type=Path)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    payload = Path(__file__).with_name("memory_probe_remote.py").read_bytes()
    side = args["side"]
    bind = "/opt/tilexr-sst-08f501a0-" + BINDS[side]
    detail = args.get("detail", "validation")
    assert detail in {"validation", "build"}
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "green-sst-30",
               "python3 -B - " + bind + " " + detail]
    result = subprocess.run(command, input=payload, capture_output=True, timeout=45)
    if result.returncode:
        print(json.dumps({"ssh_exit": result.returncode}))
        return
    print(result.stdout.decode("utf-8").strip())


if __name__ == "__main__":
    main()
