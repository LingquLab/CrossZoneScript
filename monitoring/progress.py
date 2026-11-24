"""Return bounded progress for the pinned server-30 consolidation runs."""
import argparse
import json
from pathlib import Path
import re
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
assert set(args) == {"memory", "urma"}
assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in args.values())
source = "revisions = " + repr(args) + "\n"
source += Path(__file__).with_name("progress_host.py").read_text(encoding="utf-8")
ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
       "-o", "IdentitiesOnly=yes", "-i", str(Path.home() / ".ssh/id_ed25519_server"),
       "root@141.61.94.30", "python3 -u -"]
result = subprocess.run(ssh, input=source.encode(), capture_output=True, timeout=90)
if result.returncode:
    print(json.dumps(dict(ssh_exit=result.returncode)))
else:
    print(json.dumps(json.loads(result.stdout)))
raise SystemExit(result.returncode)
