"""Run one authorized AIV-to-CCU hardware case through the retained green SSH setup."""

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
    options = parser.parse_args()
    args = json.loads(Path(options.args_file).read_text(encoding="utf-8"))
    assert args["host"] == "141.61.73.107" and args["devices"] == [6, 7]
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args["run_id"])
    setup = Path.cwd() / "relocate-pr229-141.61.73.107-ee779869-attempt2"
    assert (setup / "hosts.json").is_file() and (setup / "ssh.cmd").is_file()
    inventory = json.loads((setup / "hosts.json").read_text(encoding="utf-8"))
    cluster = inventory["clusters"]["deploy"]
    assert cluster["user"] == "root" and len(cluster["hosts"]) == 1
    assert cluster["hosts"][0]["address"] == args["host"]
    evidence = Path.cwd() / args["run_id"]
    evidence.mkdir(exist_ok=False)
    remote = Path(__file__).with_name("run_aiv_p2p_remote.py").read_text(encoding="utf-8")
    payload = base64.urlsafe_b64encode(json.dumps(args).encode()).decode("ascii")
    script = (
        "set -eo pipefail\n"
        "source /home/c00996840/ai/TileXR-deploy/current/set_env.sh >/dev/null\n"
        'python3 - "$1" <<\'CZ_AIV_P2P\'\n' + remote + "\nCZ_AIV_P2P\n"
    )
    command = [os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"), "/d", "/c",
               r"D:\works\code\ai\crosszone\TileXR\tools\tssh\tssh.cmd",
               "-c", str(setup / "hosts.json"), "-C", "deploy", "--ssh", str(setup / "ssh.cmd"),
               "--connect-timeout", "10", "--timeout", "300", "-I", "--", "timeout",
               "--signal=TERM", "--kill-after=15s", "270s", "bash", "-s", "--", payload]
    (evidence / "request.json").write_text(json.dumps(args), encoding="utf-8")
    result = subprocess.run(command, input=script.encode("utf-8"), capture_output=True, timeout=320)
    (evidence / "stdout.log").write_bytes(result.stdout)
    (evidence / "stderr.log").write_bytes(result.stderr)
    records = [json.loads(line[len("CZ_AIV_P2P "):])
               for line in result.stdout.decode("utf-8", errors="replace").splitlines()
               if line.startswith("CZ_AIV_P2P ")]
    summary = {"host": args["host"], "revision": args["revision"],
               "tssh_exit_code": result.returncode, "results": records, "evidence_directory": str(evidence)}
    (evidence / "result.json").write_text(json.dumps(summary), encoding="utf-8")
    assert result.returncode == 0 and len(records) == 1, "inspect_saved_aiv_p2p_evidence"
    assert records[0]["status"] == "PASS" and records[0]["npu_tests_run"] is True
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
