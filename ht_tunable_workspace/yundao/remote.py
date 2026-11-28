"""Build the requested revision and run the documented EP8 local HT fixture."""
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

REVISION = "0b9c3322088140c9eacb768b25edb21bda5f1161"
BRANCH = "codex/combine-ht-single-workspace"
TOOLKIT = "/usr/local/Ascend/cann-9.2.2"
root = Path.home() / "tilexr-validation" / ("ht-0b9c3322-ep8-" + str(int(time.time())))
root.mkdir(parents=True)
repo = root / "source"
build = root / "build"
result = {"revision": REVISION, "scope": "EP8 local send and gateway reduce", "log_dir": str(root)}


def command(stage, args, seconds, cwd=None, env=None):
    result["stage"] = stage
    log = root / (stage + ".log")
    try:
        with log.open("wb") as output:
            process = subprocess.run(args, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=seconds)
        if process.returncode:
            result["exit_code"] = process.returncode
            tail = log.read_text(errors="replace")[-5000:].lower()
            result["failure_kind"] = next((name for name in (
                "could not read username", "authentication failed", "repository not found", "could not resolve",
                "connection timed out", "cannot find -lascend_hal", "cmake error", "error:", "failed") if name in tail), None)
            return False
    except subprocess.TimeoutExpired:
        result["failure_kind"] = "timeout"
        return False
    return True


def device_status():
    r = subprocess.run(["npu-smi", "info"], capture_output=True, timeout=15)
    field = r.stdout.decode(errors="replace")
    idle = sorted(set(int(i) for i in re.findall(r"No running processes found in NPU\s+(\d+)", field, re.I)))
    return r.returncode, idle


def main():
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/bin/false")
    result["stage"] = "device_preflight"
    code, idle = device_status()
    result["idle_devices"] = idle
    if code or idle != list(range(8)):
        result.update(exit_code=code, failure_kind="devices_not_confirmed_idle")
        return
    if not command("clone", ["git", "clone", "--depth", "1", "--branch", BRANCH,
                             "https://github.com/LingquLab/TileXR.git", str(repo)], 75, env=env):
        return
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], timeout=8, text=True).strip()
    if head != REVISION and not command("fetch_revision", ["git", "-C", str(repo), "fetch", "--depth", "1",
                     "origin", REVISION], 55, env=env):
        return
    if not command("checkout", ["git", "-C", str(repo), "checkout", "--detach", REVISION], 12, env=env):
        return
    assert subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], timeout=8, text=True).strip() == REVISION
    if not command("dependencies", ["git", "-C", str(repo), "submodule", "update", "--init", "--depth", "1",
                                    "3rdparty/spdlog", "3rdparty/picojson"], 90, env=env):
        return
    shell = ["bash", "-c", 'source "$1/set_env.sh" >/dev/null 2>&1 || exit 66; shift; exec "$@"', "tilexr", TOOLKIT]
    configure = ["cmake", "-S", str(repo), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
                 "-DTILEXR_BUILD_COMBINE_HT=ON", "-DTILEXR_BUILD_UNITEST=OFF", "-DBUILD_TESTING=ON",
                 "-DTILEXR_COMBINE_HT_SIM_SEND=OFF", "-DTILEXR_COMBINE_HT_SST_FP32=OFF"]
    if not command("configure", shell + configure, 45):
        return
    if not command("build", shell + ["cmake", "--build", str(build), "--target", "tilexr_combine_ht_test", "-j8"], 300):
        return
    if not command("host_contract", shell + ["ctest", "--test-dir", str(build), "-R",
                                             "^tilexr_combine_ht_contract$", "--output-on-failure"], 45):
        return
    result["stage"] = "device_before_launch"
    code, idle = device_status()
    if code or idle != list(range(8)):
        result.update(exit_code=code, idle_devices=idle, failure_kind="devices_not_confirmed_idle")
        return
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    result["stage"] = "ep8"
    env = dict(os.environ, TILEXR_COMM_ID=f"127.0.0.1:{port}")
    test = str(build / "src/moonep/combine_ht/tilexr_combine_ht_test")
    jobs = []
    try:
        for rank in range(8):
            with (root / f"rank-{rank}.log").open("wb") as log:
                args = [test, "--mode", "fused", "--world", "8", "--rank", str(rank),
                        "--device", str(rank), "--local-comm", "1", "--tokens", "32", "--hidden", "128",
                        "--topk", "4", "--chunk", "32", "--splits", "4", "--reduce-begin", "8",
                        "--reducers", "8", "--iterations", "2", "--domain", "0"]
                jobs.append(subprocess.Popen(shell + args, env=env, stdout=log, stderr=subprocess.STDOUT))
        deadline = time.monotonic() + 130
        while any(job.poll() is None for job in jobs) and time.monotonic() < deadline:
            time.sleep(0.5)
    finally:
        for job in jobs:
            if job.poll() is None:
                job.terminate()
        for job in jobs:
            try:
                job.wait(timeout=3)
            except subprocess.TimeoutExpired:
                job.kill()
                job.wait()
    checks = []
    for rank, job in enumerate(jobs):
        text = (root / f"rank-{rank}.log").read_text(errors="replace")
        times = [float(v) for v in re.findall(r"latency_us=([\d.eE+-]+).*?correctness=pass", text)]
        row = dict(rank=rank, exit=job.returncode, latency_us=times)
        if job.returncode or not times:
            tail = text[-15000:].lower()
            row["first_kind"] = next((s for s in ("aclinit", "communicator", "registration", "invalid",
                                      "not supported", "partial mismatch", "not permitted", "complete failed",
                                      "error while loading", "error") if s in tail), None)
        checks.append(row)
    result["ranks"] = checks
    result["pass"] = len(checks) == 8 and all(r["exit"] == 0 and len(r["latency_us"]) == 2 for r in checks)


try:
    main()
except (OSError, ValueError, subprocess.SubprocessError, AssertionError) as error:
    result["failure_kind"] = type(error).__name__
(root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
print("TILEXR_RESULT " + json.dumps(result, ensure_ascii=True, separators=(",", ":")))
