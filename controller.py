from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import base64
import hashlib
import importlib.util
import json
import runpy
import subprocess

STATE = Path.cwd()
HERE = Path(__file__).resolve().parent
WINDOWS = Path("D:/project/UBX攻关/环境信息/ubx-tests-abi17")
HISTORICAL = STATE.parent / "813d58cc-452d-4f65-88d3-fd34818ff737"
RUNNER = HISTORICAL / "ubx-local-route-tests-b79201e/tools/ep/run_ubx_case.py"
OUTPUT = STATE / "ubx-topk-perf-20261126-build"
REMOTE = "/root/ubx-topk-perf-25a8a226-20261126"
ORIGIN = "https://github.com/LingquLab/TileXR.git"
REVISIONS = {
    "baseline": ("codex/ubx-relay-benchmark-4e2275c3", "4e2275c3003543dee60bacf071fc7ce1c717986f"),
    "candidate": ("codex/ubx-topk-recv-perf", "58eb69d6c15036d7bcf344935bb800b270c1e993"),
}


def command(args, label, timeout=180):
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    (OUTPUT / (label + ".log")).write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"{label} returned {result.returncode}")
    return result.stdout


def main():
    OUTPUT.mkdir(exist_ok=False)
    spec = importlib.util.spec_from_file_location("ep32_runner", RUNNER)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    helper = HISTORICAL / "ubx-task-rsa-key-transport.py"
    if helper.is_file():
        expected = "847b4a4801b6ddc63315aacbb092f4c0336241cb276bd96c1f954284276d129a"
        if hashlib.sha256(helper.read_bytes()).hexdigest() != expected:
            raise RuntimeError("retained SSH transport changed")
        runpy.run_path(str(helper))["install"](runner)
    else:
        import shlex
        import sys
        askpass = RUNNER.with_name("ubx_ssh_askpass.py")
        assert askpass.is_file()
        wrapper = OUTPUT / "ssh-askpass.sh"
        wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(Path(sys.executable).as_posix())
                           + " " + shlex.quote(askpass.as_posix()) + ' "$@"\n', encoding="utf-8", newline="\n")
        wrapper.chmod(0o700)
    profile = runner.load_profile(WINDOWS / "ep32-local.json")
    repo = OUTPUT / "source.git"
    command(["git", "init", str(repo)], "init")
    command(["git", "-C", str(repo), "remote", "add", "origin", ORIGIN], "remote")
    git = ["git", "-C", str(repo)]
    for label, (branch, revision) in REVISIONS.items():
        command(git + ["fetch", "--depth=2", "origin", "refs/heads/" + branch], label + "-fetch")
        assert command(git + ["rev-parse", "FETCH_HEAD"], label + "-revision").decode().strip() == revision
        command(git + ["archive", "--format=tar.gz", "--output=" + str(OUTPUT / (label + ".tgz")), revision],
                label + "-archive")
    command(git + ["checkout", "--detach", REVISIONS["candidate"][1]], "checkout")
    command(git + ["submodule", "update", "--init", "--depth", "1", "3rdparty/spdlog", "3rdparty/picojson"],
            "dependencies", 240)
    for dependency in ("spdlog", "picojson"):
        command(["git", "-C", str(repo / "3rdparty" / dependency), "archive", "--format=tar.gz",
                 "--output=" + str(OUTPUT / (dependency + ".tgz")), "HEAD"], dependency + "-archive")
    archives = {}
    for label in (*REVISIONS, "spdlog", "picojson"):
        data = (OUTPUT / (label + ".tgz")).read_bytes()
        archives[label] = [base64.b64encode(data).decode("ascii"), hashlib.sha256(data).hexdigest()]
    request = {
        "root": REMOTE,
        "revisions": {key: row[1] for key, row in REVISIONS.items()},
        "archives": archives,
        "runtime": {key: profile["runtime"][key] for key in ("cann_env",)},
        "devices": [row["devices"] for row in profile["hosts"]],
    }
    script = "REQUEST = " + repr(request) + "\n" + (HERE / "remote_build.py").read_text(encoding="utf-8")

    def run(node):
        return runner.transport(profile, node, "NODE = " + str(node) + "\n" + script, OUTPUT, "build", 1900)

    with ThreadPoolExecutor(max_workers=2) as pool:
        hosts = list(pool.map(run, range(2)))
    (OUTPUT / "result.private.json").write_text(json.dumps(hosts, indent=2), encoding="utf-8")
    summary = [{key: row[key] for key in ("status", "reason", "node", "cann", "idle", "busy",
                "indeterminate", "stages", "artifacts", "failure_stage", "error_type") if key in row} for row in hosts]
    print(json.dumps({"windows_results": str(OUTPUT), "remote_root": REMOTE, "hosts": summary},
                     ensure_ascii=True, separators=(",", ":")))
    return all(row.get("status") == "PASS" for row in hosts)


if __name__ == "__main__":
    try:
        status = main()
    except Exception as error:
        print(json.dumps({"status": "FAILED", "error_type": type(error).__name__, "results": str(OUTPUT)},
                         ensure_ascii=True))
        status = False
    raise SystemExit(0 if status else 1)
