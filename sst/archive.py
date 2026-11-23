"""Archive retained SST case artifacts on the authorized simulator host."""
import argparse
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
options = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
target = options.pop("host")
key_path = options.pop("identity")
source = "OPTIONS = " + repr(options) + "\n" + Path(__file__).with_name("archive_case.py").read_text(encoding="utf-8")
compile(source, "<sst-archive>", "exec")
command = [
    "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
    "-o", "IdentitiesOnly=yes", "-i", key_path, target, "python3 -",
]
result = subprocess.run(command, input=source.encode("utf-8"), capture_output=True, timeout=510)
if result.returncode:
    errors = result.stderr.decode("utf-8", errors="replace").splitlines()
    error = errors[-1].partition(":")[0] if errors else "remote_execution_failed"
    print(json.dumps({"ssh_exit": result.returncode, "error_class": error[:64]}))
    raise SystemExit(1)
print(result.stdout.decode("utf-8"), end="")
