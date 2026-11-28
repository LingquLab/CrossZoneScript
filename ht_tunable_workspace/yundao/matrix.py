"""Run existing HT fixtures and the shared-QP API probe on the eight-device host."""
import csv
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

import remote

HERE = Path(__file__).resolve().parent
ROOT = remote.root
PREVIOUS = ROOT.parent / "ht-0b9c3322-ep8-1795842101"
BASE_BUILD = PREVIOUS / "build"
SDK = Path(remote.TOOLKIT) / "aarch64-linux"
SHELL = ["bash", "-c", 'source "$1/set_env.sh" >/dev/null 2>&1 || exit 66; shift; exec "$@"',
         "tilexr", remote.TOOLKIT]
SUMMARY = {"revision": remote.REVISION, "log_dir": str(ROOT)}
SPECS = ["4:4", "8:8", "16:8", "8:16", "16:16", "24:24",
         "8:8+direct", "16:8+direct", "16:16+direct"]


def first_error(path):
    lines = path.read_text(errors="replace").splitlines()
    return next((re.sub(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", "[ip]", line.strip())[:180]
                 for line in lines if re.search(r"error:|\[error\]|failed|mismatch|invalid", line, re.I)), None)


def idle_devices():
    return remote.device_status()["idle_devices"]


def run_group(name, args, ranks, env, timeout):
    active = idle_devices()
    if not all(rank in active for rank in ranks):
        return None, {"status": "devices_busy", "idle": active}
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = dict(env, TILEXR_COMM_ID=f"127.0.0.1:{port}")
    folder = ROOT / name
    folder.mkdir()
    jobs = []
    timed_out = False
    try:
        for rank in ranks:
            path = folder / f"rank-{rank}.log"
            with path.open("wb") as log:
                job = subprocess.Popen(SHELL + args(rank), env=env, stdout=log, stderr=subprocess.STDOUT)
            jobs.append((job, path))
        deadline = time.monotonic() + timeout
        while any(job.poll() is None for job, _ in jobs):
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.25)
    finally:
        for job, _ in jobs:
            if job.poll() is None:
                job.terminate()
        for job, _ in jobs:
            try:
                job.wait(timeout=2)
            except subprocess.TimeoutExpired:
                job.kill()
                job.wait()
    codes = [job.returncode for job, _ in jobs]
    result = {"status": "timeout" if timed_out else "pass" if all(code == 0 for code in codes) else "failed",
              "rank_pass": sum(code == 0 for code in codes)}
    if not all(code == 0 for code in codes):
        result["first_failure"] = next((first_error(path) for job, path in jobs if job.returncode != 0), None)
    return [p.read_text(errors="replace") for _, p in jobs], result


def compile_fixture(name, source, repo, build, lib_dir, library):
    path = ROOT / name
    ok = remote.command(name, SHELL + ["c++", "-std=c++17", "-O2", str(HERE / source),
            "-I" + str(repo / "include"), "-I" + str(SDK / "include"), "-L" + str(SDK / "lib64"),
            "-L" + str(lib_dir), "-Wl,-rpath," + str(lib_dir), "-Wl,-rpath," + str(SDK / "lib64"),
            "-Wl,-rpath-link," + str(build / "src/comm"), "-l" + library, "-lascendcl", "-o", str(path)], 80)
    if not ok:
        SUMMARY.setdefault("build_errors", {})[name] = remote.result.get("first_errors", [])
    return path if ok else None


def qp_matrix(probe, env):
    rows = {}
    for spec in SPECS:
        split, _, suffix = spec.partition("+")
        six, two = split.split(":")
        direct = "7" if suffix else "0"
        logs, result = run_group("qp-" + spec, lambda rank: [str(probe), str(rank), six, two, direct],
                                 range(8), dict(env, TILEXR_UDMA_SHARED_QP_SPEC=spec), 30)
        rows[spec] = result
        if logs:
            matches = [re.search(r"^QP_RESULT (.*)$", log, re.M) for log in logs]
            obtained = [list(map(int, m.group(1).split())) for m in matches if m]
            result["api"] = sorted(set(tuple(row[:15]) for row in obtained))
    SUMMARY["qp"] = rows


def gather_matrix(build, env):
    test = str(build / "src/moonep/combine_ht/tilexr_combine_ht_test")
    rows = {}
    for world in (8, 16, 32, 64):
        for reducers in (12, 16, 24, 40):
            name = f"gather-w{world}-r{reducers}"
            def args(rank):
                return [test, "--mode", "fused", "--world", str(world), "--rank", str(rank),
                        "--device", str(rank), "--local-comm", "1", "--tokens", "32", "--hidden", "128",
                        "--topk", "4", "--chunk", "32", "--splits", "4", "--reduce-begin", "8",
                        "--reducers", str(reducers), "--iterations", "4", "--domain", "0"]
            logs, result = run_group(name, args, range(8), env, 35)
            rows[f"{world}/{reducers}"] = result
            if logs:
                samples = [[float(m) for m in re.findall(r"latency_us=([\d.eE+-]+).*?correctness=pass", l)]
                           for l in logs]
                result["checked_rounds"] = sum(len(x) for x in samples)
                steady = [x for rank in samples if len(rank) == 4 for x in rank[1:]]
                if steady:
                    result["steady_min_max_us"] = [min(steady), max(steady)]
                if result["status"] == "pass" and result["checked_rounds"] != 32:
                    result["status"] = "missing_checks"
    SUMMARY["gather"] = rows


def final_matrix(bench, env):
    rows = {}
    for tokens in (256, 4096, 16384):
        active = idle_devices()
        if not active:
            rows[tokens] = {"status": "devices_busy"}
            continue
        logs, result = run_group(f"final-t{tokens}", lambda rank: [str(bench), "--device", str(rank),
            "--world", "64", "--tokens", str(tokens), "--hidden", "128", "--active-servers", "8",
            "--blocks", "8,12,16,24", "--sync-modes", "0,1,2", "--warmup", "5", "--iters", "20", "--csv", "1"],
            active[:1], env, 110)
        rows[tokens] = result
        if logs:
            data = logs[0].splitlines()
            start = next((i for i, line in enumerate(data) if line.startswith("dtype,world,")), len(data))
            result["csv"] = list(csv.reader(data[start:]))[:13]
    SUMMARY["final"] = rows


def main():
    evidence = json.loads((PREVIOUS / "result.json").read_text())
    if evidence["revision"] != remote.REVISION or evidence.get("stage") != "ep8":
        raise ValueError("unexpected base build")
    cache = (BASE_BUILD / "CMakeCache.txt").read_text()
    repo = Path(re.search(r"^CMAKE_HOME_DIRECTORY:INTERNAL=(.+)$", cache, re.M).group(1))
    if (repo.parent / "revision.txt").read_text().strip() != remote.REVISION:
        raise ValueError("unexpected source")
    env = dict(os.environ, TILEXR_UDMA_ROOTINFO_PATH=str(remote.write_eid_rootinfo()),
               TILEXR_UDMA_SHARED_QP_SPEC="16:16")
    probe = compile_fixture("qp_probe", "qp_probe.cpp", repo, BASE_BUILD, BASE_BUILD / "src/comm", "tile-comm")
    bench = compile_fixture("final_bench", "final_bench.cpp", repo, BASE_BUILD,
                            BASE_BUILD / "src/ep/combine_ht", "tilexr-ep-ht-final-reduce")
    for _ in range(13):
        if idle_devices() == list(range(8)):
            break
        time.sleep(5)
    if probe:
        qp_matrix(probe, env)
    gather_matrix(BASE_BUILD, env)
    if bench:
        final_matrix(bench, env)


try:
    main()
except (ValueError, OSError, subprocess.SubprocessError) as exc:
    SUMMARY["failure"] = type(exc).__name__
(ROOT / "matrix.json").write_text(json.dumps(SUMMARY, indent=2) + "\n")
print("TILEXR_RESULT " + json.dumps(SUMMARY, ensure_ascii=True, separators=(",", ":")))
