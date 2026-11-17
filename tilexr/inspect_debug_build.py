"""Read a saved candidate build result and bounded compiler errors without rebuilding."""

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
    assert re.fullmatch(r"[0-9a-f]{40}", args["revision"])
    assert re.fullmatch(r"pr229-[A-Za-z0-9_-]{1,58}", args["run_id"])
    saved_path = Path.cwd() / args["run_id"] / "result.json"
    saved = json.loads(saved_path.read_text(encoding="utf-8")) if saved_path.is_file() else None
    setup = Path.cwd() / "relocate-pr229-141.61.73.107-ee779869-attempt2"
    cluster = json.loads((setup / "hosts.json").read_text(encoding="utf-8"))["clusters"]["deploy"]
    assert cluster["user"] == "root" and len(cluster["hosts"]) == 1
    assert cluster["hosts"][0]["address"] == args["host"]
    remote = Path(__file__).with_name("inspect_debug_build_remote.py").read_text(encoding="utf-8")
    payload = base64.urlsafe_b64encode(json.dumps(args).encode()).decode("ascii")
    script = 'set -euo pipefail\npython3 - "$1" <<\'CZ_READ_BUILD\'\n' + remote + "\nCZ_READ_BUILD\n"
    command = [os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"), "/d", "/c",
               r"D:\works\code\ai\crosszone\TileXR\tools\tssh\tssh.cmd",
               "-c", str(setup / "hosts.json"), "-C", "deploy", "--ssh", str(setup / "ssh.cmd"),
               "--connect-timeout", "10", "--timeout", "75", "-I", "--", "timeout",
               "--signal=TERM", "--kill-after=5s", "60s", "bash", "-s", "--", payload]
    result = subprocess.run(command, input=script.encode(), capture_output=True, timeout=90)
    records = [json.loads(line[len("CZ_BUILD_INSPECT "):])
               for line in result.stdout.decode("utf-8", errors="replace").splitlines()
               if line.startswith("CZ_BUILD_INSPECT ")]
    assert result.returncode == 0 and len(records) == 1, "build_inspection_failed"
    summary = json.dumps({"inspection_only": True, "npu_workload_run": False,
                          "saved_result": saved, "remote": records[0]})
    assert len(summary.encode()) < 24000, "build_inspection_too_large"
    print(summary)


if __name__ == "__main__":
    main()
