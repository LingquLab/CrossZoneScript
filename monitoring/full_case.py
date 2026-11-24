"""Read the completed Full SST case's failing stage and bounded diagnostics."""
import argparse
import json
from pathlib import Path
import re
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
assert set(args) == {"run_dir", "revision", "schedule"}
assert re.fullmatch(r"/opt/urma64p-cases\.[A-Za-z0-9]{8}", args["run_dir"])
assert re.fullmatch(r"[0-9a-f]{40}", args["revision"])
assert args["schedule"] in ("syncall", "clock", "nosync")
source = "request = " + repr(args) + "\n" + r"""
import json
from pathlib import Path
import re

root, name = Path(request["run_dir"]), request["schedule"]
case = root / name
scenario = json.loads((case / "scenario.json").read_text())
assert scenario["candidate_revision"] == request["revision"]
selected = {"status", "run_status", "exit", "error", "message", "returncode", "exit_code",
            "wall_seconds", "timing", "validation", "cause", "failure", "simulated_us"}
files = [case / "candidate-result.json", *sorted(case.glob("runs/*/run.json"))[-1:]]
report = {}
for path in files:
    if path.is_file():
        row = json.loads(path.read_text())
        report[path.name] = {key: value for key, value in row.items() if key in selected}
        report[path.name + "_keys"] = sorted(row)
messages = {}
paths = [root / (name + "-run.log"), *sorted(case.glob("runs/*/sim.log"))[-1:]]
for path in paths:
    if not path.is_file():
        continue
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 65536))
        text = stream.read().decode("utf-8", "replace")
    messages[path.name] = [line.strip()[:260] for line in text.splitlines()
                           if re.search(r'FATAL|Error|mismatch|timeout|not match|Simulated time', line, re.I)][-10:]
checks = []
for file_name in ("case_tools.py", "full_validation.py"):
    lines = (case / file_name).read_text().splitlines()
    for index, line in enumerate(lines):
        if "Actual kernel output differs from independent AllToAll oracle" in line:
            checks.append(dict(file=file_name, lines=lines[max(0,index-7):index+2]))
print(json.dumps(dict(revision=request["revision"], schedule=name, report=report,
                      messages=messages, relevant_check=checks)))
"""
ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
       "-o", "IdentitiesOnly=yes", "-i", str(Path.home() / ".ssh/id_ed25519_server"),
       "root@141.61.94.30", "python3 -u -"]
result = subprocess.run(ssh, input=source.encode(), capture_output=True, timeout=50)
if result.returncode:
    print(json.dumps(dict(ssh_exit=result.returncode)))
else:
    print(json.dumps(json.loads(result.stdout)))
raise SystemExit(result.returncode)
