"""Build the requested revision and run the documented EP8 local HT fixture."""
import json
import os
from pathlib import Path
import re
import socket
import sys
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
            result["first_errors"] = [line.strip()[:180] for line in log.read_text(errors="replace").splitlines()
                                      if re.search(r"error:|cannot find|undefined reference|permission denied", line, re.I)][:3]
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
    processes = 0
    section = False
    for line in field.splitlines():
        if "Process id" in line and "Process memory" in line:
            section = True
        if section and re.match(r"^\s*\|\s*\d+(?:\s+\d+)?\s*\|\s*\d+\s*\|", line):
            processes += 1
    return {"exit_code": r.returncode, "idle_devices": idle, "process_rows": processes}


def main():
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/bin/false")
    result["device_preflight"] = device_status()
    global repo
    resume = globals().get("RESUME_BUILD", False)
    if resume:
        previous = Path.home() / "tilexr-validation/ht-0b9c3322-ep8-1795841798"
        cache = (previous / "build/CMakeCache.txt").read_text()
        repo = Path(re.search(r"^CMAKE_HOME_DIRECTORY:INTERNAL=(.+)$", cache, re.M).group(1))
        assert (repo.parent / "revision.txt").read_text().strip() == REVISION
    elif len(sys.argv) == 2:
        source = Path(sys.argv[1]).resolve()
        result["stage"] = "source_bundle"
        assert (source / "revision.txt").read_text().strip() == REVISION
        assert (source / "source/3rdparty/spdlog/include/spdlog/spdlog.h").is_file()
        assert (source / "source/3rdparty/picojson/picojson.h").is_file()
        repo = source / "source"
    else:
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
    if resume:
        # The A5 SDK supplies the compiler headers. Keep the actual driver library
        # for linking and running, while avoiding an inaccessible include path.
        driver_root = root / "driver-link"
        (driver_root / "lib64").mkdir(parents=True)
        driver_lib = Path("/usr/local/Ascend/driver/lib64/driver")
        library = driver_lib / "libascend_hal.so"
        result["driver_hal_readable"] = os.access(library, os.R_OK)
        (driver_root / "lib64/driver").symlink_to(driver_lib, target_is_directory=True)
        configure.append("-DASCEND_DRIVER_PATH=" + str(driver_root))
        if not result["driver_hal_readable"]:
            result.update(stage="driver_library", failure_kind="real_hal_unreadable")
            return
    if not command("configure", shell + configure, 45):
        return
    if not command("build", shell + ["cmake", "--build", str(build), "--target", "tilexr_combine_ht_test", "-j8"], 300):
        return
    if not command("host_contract", shell + ["ctest", "--test-dir", str(build), "-R",
                                             "^tilexr_combine_ht_contract$", "--output-on-failure"], 45):
        return
    result["stage"] = "device_before_launch"
    for attempt in range(13):
        occupancy = device_status()
        if occupancy["exit_code"] == 0 and occupancy["idle_devices"] == list(range(8)):
            break
        if attempt < 12:
            time.sleep(5)
    else:
        result.update(occupancy=occupancy, failure_kind="devices_not_confirmed_idle")
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
