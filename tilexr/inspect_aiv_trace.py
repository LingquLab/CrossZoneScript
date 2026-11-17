"""Read retained AIV/CCU trace fields on the selected green host without a workload."""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--args-file", required=True)
    args = json.loads(Path(parser.parse_args().args_file).read_text(encoding="utf-8"))
    assert args["host"] == "141.61.73.107"
    assert re.fullmatch(r"pr229-[A-Za-z0-9_-]{1,58}", args["run_id"])
    setup = Path.cwd() / "relocate-pr229-141.61.73.107-ee779869-attempt2"
    inventory = json.loads((setup / "hosts.json").read_text(encoding="utf-8"))
    cluster = inventory["clusters"]["deploy"]
    assert cluster["user"] == "root" and len(cluster["hosts"]) == 1
    assert cluster["hosts"][0]["address"] == args["host"]
    remote = Path(__file__).with_name("inspect_aiv_trace_remote.py").read_text(encoding="utf-8")
    payload = base64.urlsafe_b64encode(json.dumps(args).encode()).decode("ascii")
    script = 'set -euo pipefail\npython3 - "$1" <<\'CZ_READ_TRACE\'\n' + remote + "\nCZ_READ_TRACE\n"
    command = [os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"), "/d", "/c",
               r"D:\works\code\ai\crosszone\TileXR\tools\tssh\tssh.cmd",
               "-c", str(setup / "hosts.json"), "-C", "deploy", "--ssh", str(setup / "ssh.cmd"),
               "--connect-timeout", "10", "--timeout", "75", "-I", "--", "timeout",
               "--signal=TERM", "--kill-after=5s", "60s", "bash", "-s", "--", payload]
    result = subprocess.run(command, input=script.encode(), capture_output=True, timeout=90)
    records = [json.loads(line[len("CZ_TRACE_INSPECT "):])
               for line in result.stdout.decode("utf-8", errors="replace").splitlines()
               if line.startswith("CZ_TRACE_INSPECT ")]
    assert result.returncode == 0 and len(records) == 1, "trace_inspection_failed"
    output = json.dumps({"host": args["host"], "inspection_only": True, "result": records[0]})
    assert len(output.encode()) < 28000, "trace_summary_too_large"
    print(output)


if __name__ == "__main__":
    main()
