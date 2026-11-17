"""Finish the inspected candidate package and run the selected two-rank smoke once."""

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
    request = json.loads(Path(parser.parse_args().args_file).read_text(encoding="utf-8"))
    assert request["host"] == "141.61.73.107" and request["devices"] == [2, 3]
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request["run_id"])
    previous = Path.cwd() / "pr229-aiv-debug-build-107-23-20261117-01"
    args = json.loads((previous / "request.json").read_text(encoding="utf-8"))
    assert args["revision"] == request["revision"] == "ab9a3e61da5a39fa5daafc08960d3ced7bf58ca0"
    args.update(request, resume_verified_build=True)
    evidence = Path.cwd() / args["run_id"]
    evidence.mkdir(exist_ok=False)
    (evidence / "request.json").write_text(json.dumps(args), encoding="utf-8")
    resources = {name: base64.b64encode(Path(__file__).with_name(name).read_bytes()).decode("ascii")
                 for name in ["build_debug_remote.py", "run_aiv_p2p_remote.py"]}
    payload = base64.urlsafe_b64encode(json.dumps(args).encode()).decode("ascii")
    remote = r'''
import base64,json,sys
from pathlib import Path
args=json.loads(base64.urlsafe_b64decode(sys.argv[1]))
resources=__CZ_RESOURCES__
summary={"status":"FAIL","npu_tests_run":False}
stage="build_result"
try:
    builder={"__name__":"build_helpers"}
    exec(compile(base64.b64decode(resources["build_debug_remote.py"]),"build_debug_remote.py","exec"),builder)
    built={"status":"FAIL","npu_workload_run":False}
    summary[stage]=built
    builder["run"](args,built)
    root=Path(built["build_directory"])
    (root/"resume-build-result.json").write_text(json.dumps(built,indent=2)+"\n")
    test=dict(args,release=built["package"],artifact_hashes=built["artifact_hashes"],
              runner_sha256=built["runner_sha256"],guard_sha256=built["guard_sha256"])
    runner={"__name__":"hardware_helpers"}
    exec(compile(base64.b64decode(resources["run_aiv_p2p_remote.py"]),"run_aiv_p2p_remote.py","exec"),runner)
    stage="hardware_result"
    hardware={"status":"FAIL","stage":"preflight","revision":args["revision"],"devices":[2,3],"npu_tests_run":False}
    summary[stage]=hardware
    runner["run"](test,hardware)
    summary["status"]="PASS"
except Exception as error:
    summary.update(error_type=type(error).__name__,reason=str(error)[:160],failed_stage=stage)
finally:
    hardware=summary.get("hardware_result",{})
    summary["npu_tests_run"]=hardware.get("npu_tests_run",False)
    if "work_directory" in hardware:
        (Path(hardware["work_directory"])/"result.json").write_text(json.dumps(hardware,indent=2)+"\n")
    root=Path('/home/c00996840/ai/TileXR-pr229-debug-ab9a3e61da5a')
    (root/(args["run_id"]+"-result.json")).write_text(json.dumps(summary,indent=2)+"\n")
print("CZ_RESUME_DEBUG "+json.dumps(summary))
sys.exit(0 if summary["status"]=="PASS" else 1)
'''.replace("__CZ_RESOURCES__", repr(resources))
    script = ("set -eo pipefail\n"
              "source /home/c00996840/ai/TileXR-deploy/current/set_env.sh >/dev/null\n"
              'python3 - "$1" <<\'CZ_RESUME_PY\'\n' + remote + "\nCZ_RESUME_PY\n")
    setup = Path.cwd() / "relocate-pr229-141.61.73.107-ee779869-attempt2"
    inventory = json.loads((setup / "hosts.json").read_text(encoding="utf-8"))["clusters"]["deploy"]
    assert inventory["user"] == "root" and len(inventory["hosts"]) == 1
    assert inventory["hosts"][0]["address"] == args["host"]
    argv = [os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"), "/d", "/c",
            r"D:\works\code\ai\crosszone\TileXR\tools\tssh\tssh.cmd",
            "-c", str(setup / "hosts.json"), "-C", "deploy", "--ssh", str(setup / "ssh.cmd"),
            "--connect-timeout", "10", "--timeout", "420", "-I", "--", "timeout",
            "--signal=TERM", "--kill-after=10s", "390s", "bash", "-s", "--", payload]
    result = subprocess.run(argv, input=script.encode(), capture_output=True, timeout=450)
    (evidence / "stdout.log").write_bytes(result.stdout)
    (evidence / "stderr.log").write_bytes(result.stderr)
    records = [json.loads(line[len("CZ_RESUME_DEBUG "):])
               for line in result.stdout.decode("utf-8", errors="replace").splitlines()
               if line.startswith("CZ_RESUME_DEBUG ")]
    summary = {"host": args["host"], "tssh_exit_code": result.returncode,
               "results": records, "evidence_directory": str(evidence)}
    (evidence / "result.json").write_text(json.dumps(summary), encoding="utf-8")
    assert result.returncode == 0 and len(records) == 1, "inspect_saved_resume_evidence"
    assert records[0]["status"] == "PASS" and records[0]["npu_tests_run"] is True
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
