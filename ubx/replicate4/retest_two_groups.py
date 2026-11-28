"""Build and run the EP32 two-group RDMA payload benchmark."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import shlex
import statistics
import tempfile
import time

import benchmark
import build
import relay24_traces

HOSTS = ("90.90.93.81", "90.90.93.83")
REVISION = "af8c365875d991cb42dc8079412d626ad7d7ffc4"
LABEL = "replicate4-rdma-two-groups-bs32-" + REVISION[:7]
ROOT = PurePosixPath("/root/ubx-" + LABEL)
PORT = 32500
HERE = Path(__file__).resolve().parent
TOOLKIT = "/usr/local/Ascend/cann-9.2.T101/set_env.sh"

build.CANDIDATE = REVISION
benchmark.HOSTS = HOSTS
benchmark.REVISION = REVISION
benchmark.ROOT = ROOT
benchmark.HARDWARE = ROOT / "source/dispatch/dispatch-release/tests/hardware"
benchmark.RUNTIME_INSTALL = ROOT / "runtime-install"
benchmark.RUNTIME_REVISION = build.RUNTIME


def build_host(host, bundle, digest):
    peer = "root@" + host
    staging = ROOT / "staged-input"
    build.remote(host, build.SSH, peer, f"mkdir -p {ROOT} && mkdir {staging}")
    build.remote(host, build.SSH.with_name("scp.exe"), str(bundle),
                 f"{peer}:{staging}/sources.tar", timeout=300)
    script = (HERE / "build_two_groups.sh").read_bytes().replace(b"\r\n", b"\n")
    command = shlex.join(["bash", "-s", "--", str(ROOT), REVISION, digest])
    receipt = json.loads(benchmark.ssh(host, command, payload=script, timeout=1000, stage="build"))
    assert (receipt["built"], receipt["revision"], receipt["runtime_revision"], receipt["clock_revision"]) == (
        True, REVISION, build.RUNTIME, build.CLOCK)
    return receipt


def prepare(pool):
    with tempfile.TemporaryDirectory(prefix="ubx-two-groups-", dir=Path.cwd()) as temporary:
        bundle = build.prepare(Path(temporary).resolve())
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        builds = list(pool.map(lambda host: build_host(host, bundle, digest), HOSTS))
    assert builds[0]["fixture_sha256"] == builds[1]["fixture_sha256"]
    return builds


def verify_runtime_receipt(host):
    code = (
        "import json, pathlib, sys; root=pathlib.Path(sys.argv[1]); "
        "item=json.loads((root/'build-result.json').read_text()); "
        "assert item['runtime_revision']==sys.argv[2]; "
        "(root/'runtime-result.json').write_text(json.dumps(dict("
        "revision=item['runtime_revision'],runtime_sha256=item['runtime_sha256'])))"
    )
    benchmark.ssh(host, shlex.join(["python3", "-c", code, str(ROOT), build.RUNTIME]))


def controller(index, action):
    command = shlex.join([
        "python3", "-", action, str(index), str(PORT), benchmark.OUTPUT.name,
        str(benchmark.RUNTIME_INSTALL), build.RUNTIME, str(ROOT), REVISION, "replicate4", "require-ok",
    ])
    if action == "run":
        lib = ":".join(str(path) for path in (
            benchmark.RUNTIME_INSTALL / "lib", benchmark.RUNTIME_INSTALL / "lib64", ROOT / "dispatch-build"))
        command = shlex.join(["bash", "-lc",
                              f"source {TOOLKIT} >/dev/null && "
                              f'export LD_LIBRARY_PATH={shlex.quote(lib)}:"${{LD_LIBRARY_PATH:-}}" && exec {command}'])
    rounds = (HERE / "round_count.py").read_text().split('if __name__ == "__main__":', 1)[0]
    remote = (HERE / "run_remote.py").read_text().replace('"90.90.93.188"', f'"{HOSTS[0]}"')
    payload = (rounds + "\n" + remote).encode()
    return json.loads(benchmark.ssh(HOSTS[index], command, payload=payload,
                                    timeout=740 if action == "run" else 215, stage=action))


def wait_idle(pool, idle_wait):
    deadline = time.monotonic() + idle_wait
    while True:
        futures = [pool.submit(controller, index, "preflight") for index in range(2)]
        busy = []
        for future in futures:
            try:
                receipt = future.result()
                assert receipt["idle"] == receipt["healthy"] == 16
            except RuntimeError as error:
                if "NPU occupied after preflight" not in str(error):
                    raise
                busy.append(str(error))
        if not busy:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(" / ".join(busy))
        time.sleep(10)


def collect(pool, destination):
    result = destination / LABEL
    assert destination.is_absolute() and destination.is_dir() and not result.exists()
    with tempfile.TemporaryDirectory(prefix=".ubx-two-groups-", dir=destination) as temporary:
        collection = Path(temporary) / "verified"
        collection.mkdir()
        summary = relay24_traces.collect(pool, collection)
        route_hashes = set()
        totals = []
        final_peak = (0.0, -1, -1)
        for rank in range(32):
            trace = json.loads((collection / f"rank{rank}/dispatch.last.trace.json").read_bytes())
            metadata = trace["metadata"]
            assert metadata["payload_distribution"] == "rank_group_8"
            route_hashes.add(metadata["route_sha256"])
            durations = [(event["dur"], rank, event["tid"]) for event in trace["traceEvents"]
                         if event.get("ph") == "X" and event["name"] == "core_total"]
            assert len(durations) == 56
            final_peak = max(final_peak, *durations)
            totals.extend(item[0] for item in durations)
        route_hash, = route_hashes
        us, rank, core = final_peak
        summary.update(payload_distribution="rank_group_8", profile_mode="full",
                       route_distribution="replicate4", route_qps_verified=128,
                       coordinator_port=PORT, hosts=list(HOSTS), runtime_revision=build.RUNTIME,
                       route_sha256=route_hash, core_total_samples=32*50*56, local_results=str(result),
                       trace_count=32+int(summary["slowest"]["round"] != 59),
                       last_round=dict(rank=rank, aiv=core, max_us=us, mean_us=statistics.mean(totals)))
        (collection / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        collection.rename(result)
    return summary


def main(args):
    benchmark.OUTPUT = ROOT / args.get("run_name", "full-bs32-f50")
    with ThreadPoolExecutor(max_workers=2) as pool:
        if args.get("mode", "initial") == "initial":
            prepare(pool)
        if args.get("mode") != "collect":
            list(pool.map(verify_runtime_receipt, HOSTS))
            wait_idle(pool, int(args.get("idle_wait", 240)))
            results = list(pool.map(lambda index: controller(index, "run"), range(2)))
            assert all(result["passed"] and result["route_qps_verified"] == 64 for result in results)
        return collect(pool, Path(args["destination"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    print(json.dumps(main(args), separators=(",", ":")))
