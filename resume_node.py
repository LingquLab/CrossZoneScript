import base64
import hashlib
import importlib.util
import json
import sys
import runpy
from pathlib import Path

from controller import OUTPUT, REMOTE, REVISIONS, RUNNER, WINDOWS
from key_transport import install, make_profile
from controller import HISTORICAL


def main():
    receipt = OUTPUT / "result.private.json"
    rows = json.loads(receipt.read_text())
    assert rows[0]["status"] == "PASS" and rows[1]["status"] == "BLOCKED"
    spec = importlib.util.spec_from_file_location("ep32_runner", RUNNER)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    profile = make_profile(runner, WINDOWS / "ep32-local.json", OUTPUT)
    helper = HISTORICAL / "ubx-task-rsa-key-transport.py"
    fallback = None
    if helper.is_file():
        assert hashlib.sha256(helper.read_bytes()).hexdigest() == "847b4a4801b6ddc63315aacbb092f4c0336241cb276bd96c1f954284276d129a"
        runpy.run_path(str(helper))["install"](runner)
        fallback = runner.transport
    run = install(runner, profile, fallback)
    node0 = run(profile, 0, 'import json; print(json.dumps({"status":"PASS"}))', OUTPUT, "verify-node0", 40)
    if node0.get("status") != "PASS":
        return node0
    preflight = run(profile, 1,
                    f'import json,os\nprint(json.dumps({{"status":"PASS","exists":os.path.exists({REMOTE!r})}}))\n',
                    OUTPUT, "retry-preflight", 40)
    if preflight.get("status") != "PASS" or preflight["exists"]:
        return preflight
    archives = {}
    for name in (*REVISIONS, "spdlog", "picojson"):
        data = (OUTPUT / (name + ".tgz")).read_bytes()
        archives[name] = [base64.b64encode(data).decode("ascii"), hashlib.sha256(data).hexdigest()]
    request = {
        "root": REMOTE, "revisions": {key: value[1] for key, value in REVISIONS.items()},
        "archives": archives, "runtime": {"cann_env": profile["runtime"]["cann_env"]},
        "devices": [entry["devices"] for entry in profile["hosts"]],
    }
    remote = Path(__file__).with_name("remote_build.py").read_text(encoding="utf-8")
    status = run(profile, 1, f"NODE = 1\nREQUEST = {request!r}\n" + remote, OUTPUT, "build-retry", 1900)
    if status["status"] == "PASS":
        rows[1] = status
        temporary = receipt.with_suffix(".tmp")
        temporary.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        temporary.replace(receipt)
    return {key: status[key] for key in ("status", "reason", "error_type", "node", "cann", "idle", "busy",
            "indeterminate", "failure_stage", "stages", "exists", "exit_code") if key in status}


if __name__ == "__main__":
    try:
        result = main()
    except Exception as error:
        result = {"status": "BLOCKED", "error_type": type(error).__name__}
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
