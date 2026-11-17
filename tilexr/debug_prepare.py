"""Collect the prepare failure and create project-local topology on the authorized host."""

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
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args["run_id"])
    setup = Path.cwd() / "relocate-pr229-141.61.73.107-ee779869-attempt2"
    inventory = json.loads((setup / "hosts.json").read_text(encoding="utf-8"))
    cluster = inventory["clusters"]["deploy"]
    assert cluster["user"] == "root" and len(cluster["hosts"]) == 1
    assert cluster["hosts"][0]["address"] == args["host"]
    evidence = Path.cwd() / args["run_id"]
    evidence.mkdir(exist_ok=False)
    remote = Path(__file__).with_name("debug_prepare_remote.py").read_text(encoding="utf-8")
    payload = base64.urlsafe_b64encode(json.dumps(args).encode()).decode("ascii")
    script = (
        "set -eo pipefail\n"
        "source /home/c00996840/ai/TileXR-deploy/current/set_env.sh >/dev/null\n"
        'python3 - "$1" <<\'CZ_DEBUG_PREPARE\'\n' + remote + "\nCZ_DEBUG_PREPARE\n"
    )
    command = [os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"), "/d", "/c",
               r"D:\works\code\ai\crosszone\TileXR\tools\tssh\tssh.cmd",
               "-c", str(setup / "hosts.json"), "-C", "deploy", "--ssh", str(setup / "ssh.cmd"),
               "--connect-timeout", "10", "--timeout", "240", "-I", "--", "timeout",
               "--signal=TERM", "--kill-after=5s", "220s", "bash", "-s", "--", payload]
    (evidence / "request.json").write_text(json.dumps(args), encoding="utf-8")
    result = subprocess.run(command, input=script.encode("utf-8"), capture_output=True, timeout=260)
    (evidence / "stdout.log").write_bytes(result.stdout)
    (evidence / "stderr.log").write_bytes(result.stderr)
    records = [json.loads(line[len("CZ_DEBUG_PREPARE "):])
               for line in result.stdout.decode("utf-8", errors="replace").splitlines()
               if line.startswith("CZ_DEBUG_PREPARE ")]
    summary = {"host": args["host"], "tssh_exit_code": result.returncode,
               "results": records, "evidence_directory": str(evidence)}
    (evidence / "result.json").write_text(json.dumps(summary), encoding="utf-8")
    assert result.returncode == 0 and len(records) == 1, "inspect_saved_prepare_evidence"
    assert records[0]["status"] == "PASS"
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
