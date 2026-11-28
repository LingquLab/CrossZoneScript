"""Run existing HT fixtures and the shared-QP API probe on the eight-device host."""
import csv
import json
import os
from pathlib import Path
import re
import socket
import shlex
import subprocess
import sys
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


def run_group(name, args, ranks, env, timeout, barriers=False):
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
                job = subprocess.Popen(SHELL + args(rank), env=env, stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.PIPE if barriers else subprocess.DEVNULL)
            jobs.append((job, path))
        deadline = time.monotonic() + timeout
        fence = 0
        while any(job.poll() is None for job, _ in jobs):
            if barriers:
                if any(job.poll() not in (None, 0) for job, _ in jobs):
                    break
                marker = ("LAUNCH_READY" if fence % 2 == 0 else "ROUND_READY") + f" {fence // 2}\n"
                if fence < 8 and all(marker in p.read_text(errors="replace") for _, p in jobs):
                    for job, _ in jobs:
                        job.stdin.write(b"go\n")
                        job.stdin.flush()
                    fence += 1
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
        result["first_failure"] = next((reason for job, path in jobs if job.returncode != 0
                                         if (reason := first_error(path))), None)
    return [p.read_text(errors="replace") for _, p in jobs], result


def compile_fixture(name, source, repo, build, lib_dir, library, extra=()):
    path = ROOT / name
    ok = remote.command(name, SHELL + ["c++", "-std=c++17", "-O2", str(HERE / source),
            "-I" + str(repo / "include"), "-I" + str(SDK / "include"), "-L" + str(SDK / "lib64"),
            "-L" + str(lib_dir), "-Wl,-rpath," + str(lib_dir), "-Wl,-rpath," + str(SDK / "lib64"),
            "-Wl,-rpath-link," + str(build / "src/comm"), "-l" + library, "-lascendcl", *extra, "-o", str(path)], 80)
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


def gather_matrix(build, env, profiling=False, previous=None):
    test = str(build / "src/moonep/combine_ht/tilexr_combine_ht_test")
    rows = {}
    for world in ((8,) if profiling else (8, 16, 32, 64)):
        for reducers in (12, 16, 24, 40):
            key = f"{world}/{reducers}"
            if previous and previous.get(key, {}).get("status") == "pass":
                continue
            name = f"gather-w{world}-r{reducers}"
            def args(rank):
                command = [test, "--mode", "fused", "--world", str(world), "--rank", str(rank),
                        "--device", str(rank), "--local-comm", "1", "--tokens", "32", "--hidden", "128",
                        "--topk", "4", "--chunk", "32", "--splits", "4", "--reduce-begin", "8",
                        "--reducers", str(reducers), "--iterations", "4", "--domain", "0"]
                if profiling:
                    command += ["--profile", str(ROOT / name / "gather")]
                return command
            logs, result = run_group(name, args, range(8), env, 35)
            rows[f"{world}/{reducers}"] = result
            if logs:
                samples = [[float(m) for m in re.findall(r"latency_us=([\d.eE+-]+).*?correctness=pass", l)]
                           for l in logs]
                result["checked_rounds"] = sum(len(x) for x in samples)
                steady = [x for rank in samples if len(rank) == 4 for x in rank[1:]]
                if steady:
                    result["steady_min_max_us"] = [min(steady), max(steady)]
                if profiling:
                    result["record_files"] = sum(path.stat().st_size > 0 for path in
                                                 (ROOT / name).glob("gather.*.records"))
                if result["status"] == "pass" and (result["checked_rounds"] != 32 or
                                                 profiling and result["record_files"] != 32):
                    result["status"] = "missing_checks"
    SUMMARY["gather_profile" if profiling else "gather"] = rows


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


def compile_device_probe(repo, target, source):
    stem = f"tilexr_ep_ht_{target}_kernel"
    rule = BASE_BUILD / f"src/ep/combine_ht/CMakeFiles/{stem}.dir/build.make"
    line = next(line.split(" && ", 1)[1] for line in rule.read_text().splitlines()
                if "bisheng" in line and " -xcce " in line)
    args = shlex.split(line)
    output = ROOT / (Path(source).stem + ".o")
    args[args.index("-o") + 1] = str(output)
    args[args.index("-c") + 1] = str(HERE / source)
    args.extend(["-I" + str(repo / "src/ep/combine_ht")])
    if not remote.command(output.stem, SHELL + args, 180, cwd=ROOT):
        SUMMARY.setdefault("build_errors", {})[output.stem] = remote.result.get("first_errors", [])
        return None
    return output


def timed_module(case, args, env):
    logs, result = run_group(case, args, range(8), env, 65, barriers=True)
    if logs:
        events = [[m.split() for m in re.findall(r"^MODULE_RESULT (.*)$", log, re.M)] for log in logs]
        result["checked_rounds"] = sum(map(len, events))
        if result["checked_rounds"] == 32:
            result["steps"] = int(events[0][0][1])
            result["local_ready_stride"] = int(events[0][0][2])
            steady = [float(event[3]) for rank in events for event in rank[1:]]
            result["steady_min_max_us"] = [min(steady), max(steady)]
        elif result["status"] == "pass":
            result["status"] = "missing_checks"
    return result


def module_case(executable, env, mode, tokens, sync, cores, spec, binary):
    case = f"{mode}-t{tokens}-s{sync}-c{cores}-q{spec}"
    return timed_module(case, lambda rank: [str(executable), mode, str(rank), str(tokens), str(sync),
                        str(cores), str(binary)], dict(env, TILEXR_UDMA_SHARED_QP_SPEC=spec))


def concurrent_matrix(repo, env):
    lib_dir = BASE_BUILD / "src/moonep/combine_ht"
    ep_dir = BASE_BUILD / "src/ep/combine_ht"
    comm_dir = BASE_BUILD / "src/comm"
    executable = compile_fixture("ep8_concurrent", "ep8_concurrent.cpp", repo, BASE_BUILD,
        lib_dir, "tilexr-moonep-combine-ht",
        ("-DTILEXR_COMBINE_HT_PIPELINE=1", "-DTILEXR_ENABLE_PROFILING=0", "-I" + str(repo / "tests/moonep_combine_ht"),
         "-I" + str(repo / "src/moonep/combine_ht/common"), "-I" + str(repo / "src/ep/combine_ht"),
         "-I" + str(SDK / "pkg_inc"), "-I" + str(SDK / "pkg_inc/runtime"),
         "-L" + str(comm_dir), "-Wl,-rpath," + str(comm_dir), "-ltile-comm", "-lruntime",
         "-L" + str(ep_dir), "-Wl,-rpath," + str(ep_dir), "-ltilexr-moonep-combine-ht-pipeline",
         "-ltilexr-ep-ht-local-return", "-ltilexr-ep-ht-final-reduce", "-ltilexr-ep-ht-send"))
    if not executable:
        return
    SUMMARY["concurrent_build"] = "pass"
    for _ in range(24):
        if idle_devices() == list(range(8)):
            break
        time.sleep(5)
    rows = SUMMARY["concurrent"] = {}
    pipeline_obj = lib_dir / "tilexr_moonep_combine_ht_kernel.o"
    local_obj = ep_dir / "tilexr_ep_ht_local_return_kernel.o"
    for tokens in (32, 1024):
        for sync in range(3):
            for mode in ("concurrent", "serial"):
                name = f"{mode}-t{tokens}-s{sync}"
                rows[name] = timed_module(name, lambda rank: [str(executable), str(rank), str(tokens),
                    str(sync), str(int(mode == "concurrent")), str(pipeline_obj), str(local_obj)],
                    dict(env, TILEXR_UDMA_SHARED_QP_SPEC="8:8+direct"))



def native_modules(repo, env):
    lib_dir = BASE_BUILD / "src/ep/combine_ht"
    executable = compile_fixture("ep8_modules", "ep8_modules.cpp", repo, BASE_BUILD,
                                 lib_dir, "tilexr-ep-ht-local-return",
                                 ("-I" + str(repo / "src/ep/combine_ht"), "-I" + str(SDK / "pkg_inc"),
                                  "-I" + str(SDK / "pkg_inc/runtime"), "-L" + str(BASE_BUILD / "src/comm"),
                                  "-Wl,-rpath," + str(BASE_BUILD / "src/comm"), "-ltile-comm", "-lruntime"))
    if not executable:
        return
    send_kernel = compile_device_probe(repo, "inter_server_send", "native_send_probe.cpp")
    send_results = SUMMARY["native_send"] = {}
    if send_kernel:
        for cores in (1, 2, 4, 8):
            for count in (4, 8, 16):
                if count < cores:
                    continue
                spec = f"{count}:{count}"
                send_results[f"{cores}/{spec}"] = module_case(executable, env, "send", 1024, 2,
                                                              cores, spec, send_kernel)
                if send_results[f"{cores}/{spec}"]["status"] == "timeout":
                    break
    local_results = SUMMARY["local_return"] = {}
    local_kernel = lib_dir / "tilexr_ep_ht_local_return_kernel.o"
    for mode in ("fullmesh", "direct"):
        for tokens in (4096, 16384):
            for sync in range(3):
                local_results[f"{mode}/{tokens}/{sync}"] = module_case(
                    executable, env, mode, tokens, sync, 8, "8:8+direct", local_kernel)



def final_wait_matrix(repo, env):
    probe = compile_device_probe(repo, "final_reduce", "final_probe.cpp")
    bench = compile_fixture("final_wait_bench", "final_bench.cpp", repo, BASE_BUILD,
                            BASE_BUILD / "src/ep/combine_ht", "tilexr-ep-ht-final-reduce",
                            ("-DTILEXR_FINAL_PROBE=1", "-I" + str(SDK / "pkg_inc"), "-I" + str(SDK / "pkg_inc/runtime"), "-lruntime"))
    if not (probe and bench):
        return
    env = dict(env, TILEXR_FINAL_PROBE_OBJECT=str(probe))
    rows = SUMMARY["final_wait"] = {}
    for tokens in (32, 256, 4096, 16384):
        for _ in range(24):
            active = idle_devices()
            if active:
                break
            time.sleep(5)
        if not active:
            rows[tokens] = {"status": "devices_busy"}
            break
        logs, result = run_group(f"final-t{tokens}", lambda rank: [str(bench), "--device", str(rank),
            "--world", "64", "--tokens", str(tokens), "--hidden", "128", "--active-servers", "8",
            "--blocks", "8,12,16,24", "--sync-modes", "0,1,2,3,4,5", "--rotation-policy", "1",
            "--warmup", "5", "--iters", "30", "--csv", "1"], active[:1], env, 110)
        rows[tokens] = result
        if logs:
            parsed = (row for row in csv.reader(logs[0].splitlines()) if len(row) == 20 and row[0] == "bf16")
            result["blocks_mode_waitsteps_p50_p95_verify"] = [
                [int(row[8]), row[9], int(row[10]), float(row[13]), float(row[15]), int(row[19])]
                for row in parsed]
            if result["status"] == "pass" and (len(result["blocks_mode_waitsteps_p50_p95_verify"]) != 24 or
                not all(row[-1] == 1 for row in result["blocks_mode_waitsteps_p50_p95_verify"])):
                result["status"] = "missing_checks"



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
    if sys.argv[1:] == ["final_wait"]:
        final_wait_matrix(repo, env)
        return
    if sys.argv[1:] == ["concurrent"]:
        concurrent_matrix(repo, env)
        return
    if sys.argv[1:] == ["finish"]:
        initial = ROOT.parent / "ht-0b9c3322-ep8-1795846177"
        for _ in range(24):
            if idle_devices():
                break
            time.sleep(5)
        final_matrix(initial / "final_bench", env)
        previous = json.loads((initial / "matrix.json").read_text())["gather"]
        gather_matrix(BASE_BUILD, env, previous=previous)
        return
    if sys.argv[1:] in (["profile"], ["remaining"]):
        build = ROOT / "profile-build"
        configure = SHELL + ["cmake", "-S", str(repo), "-B", str(build),
            "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_TESTING=ON", "-DTILEXR_BUILD_COMBINE_HT=ON",
            "-DTILEXR_BUILD_UNITEST=OFF", "-DTILEXR_ENABLE_PROFILING=ON",
            "-DASCEND_DRIVER_PATH=" + str(PREVIOUS / "driver-link")]
        profile_built = remote.command("profile_configure", configure, 45) and remote.command("profile_build",
                SHELL + ["cmake", "--build", str(build), "--target", "tilexr_combine_ht_test", "-j8"], 300)
        SUMMARY["profile_built"] = profile_built
        if not profile_built:
            SUMMARY["profile_build_error"] = [remote.result.get("stage"), remote.result.get("first_errors")]
        if sys.argv[1:] == ["remaining"]:
            initial = ROOT.parent / "ht-0b9c3322-ep8-1795846177"
            for _ in range(24):
                if idle_devices():
                    break
                time.sleep(5)
            final_matrix(initial / "final_bench", env)
            previous = json.loads((initial / "matrix.json").read_text())["gather"]
            gather_matrix(BASE_BUILD, env, previous=previous)
        if profile_built:
            gather_matrix(build, env, True)
        return
    if sys.argv[1:] in (["modules"], ["device"]):
        for _ in range(24):
            if idle_devices() == list(range(8)):
                break
            time.sleep(5)
        native_modules(repo, env)
        if sys.argv[1:] == ["device"]:
            concurrent_matrix(repo, env)
        return
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
except (ValueError, OSError, subprocess.SubprocessError, StopIteration) as exc:
    SUMMARY["failure"] = [type(exc).__name__, str(exc)[:150]]
(ROOT / "matrix.json").write_text(json.dumps(SUMMARY, indent=2) + "\n")
print("TILEXR_RESULT " + json.dumps(SUMMARY, ensure_ascii=True, separators=(",", ":")))
