"""Run the single authorized CCU environment qualification through the retained SSH setup."""

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
    assert args["host"] == "141.61.73.107" and args["devices"] == [2, 3]
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args["run_id"])
    evidence = Path.cwd() / args["run_id"]
    evidence.mkdir(exist_ok=False)
    (evidence / "request.json").write_text(json.dumps(args), encoding="utf-8")
    setup = Path.cwd() / "relocate-pr229-141.61.73.107-ee779869-attempt2"
    inventory = json.loads((setup / "hosts.json").read_text(encoding="utf-8"))["clusters"]["deploy"]
    assert inventory["user"] == "root" and len(inventory["hosts"]) == 1
    assert inventory["hosts"][0]["address"] == args["host"]
    remote = Path(__file__).with_name("hccl_baseline_remote.py").read_text(encoding="utf-8")
    payload = base64.urlsafe_b64encode(json.dumps(args).encode()).decode("ascii")
    script = ("set -eo pipefail\n"
              "source /home/c00996840/ai/TileXR-deploy/current/set_env.sh >/dev/null\n"
              'python3 - "$1" <<\'CZ_BASELINE_PY\'\n' + remote + "\nCZ_BASELINE_PY\n")
    argv = [os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"), "/d", "/c",
            r"D:\works\code\ai\crosszone\TileXR\tools\tssh\tssh.cmd",
            "-c", str(setup / "hosts.json"), "-C", "deploy", "--ssh", str(setup / "ssh.cmd"),
            "--connect-timeout", "10", "--timeout", "300", "-I", "--", "timeout",
            "--signal=TERM", "--kill-after=10s", "270s", "bash", "-s", "--", payload]
    result = subprocess.run(argv, input=script.encode(), capture_output=True, timeout=330)
    (evidence / "stdout.log").write_bytes(result.stdout)
    (evidence / "stderr.log").write_bytes(result.stderr)
    records = [json.loads(line[len("CZ_HCCL_BASELINE "):])
               for line in result.stdout.decode("utf-8", errors="replace").splitlines()
               if line.startswith("CZ_HCCL_BASELINE ")]
    summary = {"host": args["host"], "tssh_exit_code": result.returncode,
               "results": records, "evidence_directory": str(evidence)}
    (evidence / "result.json").write_text(json.dumps(summary), encoding="utf-8")
    assert result.returncode == 0 and len(records) == 1, "inspect_saved_hccl_baseline"
    assert records[0]["status"] == "PASS" and records[0]["npu_workload_run"] is True
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
