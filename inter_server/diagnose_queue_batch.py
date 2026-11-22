"""Read bounded output and queue evidence from a completed SST case."""

import json
import argparse
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--args-file", type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text())
case = args["case"]
assert case.startswith(("t4-", "t8-", "t257-")) and case.replace("-", "").isalnum()
source = (Path(__file__).with_name("diagnose_queue_batch_remote.py").read_text()
          + "\nprint(json.dumps(diagnose(" + repr(case) + "), separators=(',', ':')))\n")
command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]
proc = subprocess.run(command, input=source.encode(), capture_output=True, timeout=75, check=True)
print(json.dumps(json.loads(proc.stdout), separators=(",", ":")))
