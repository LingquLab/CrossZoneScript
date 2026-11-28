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
DEFAULT_HOSTS = HOSTS
DEFAULT_LABEL = LABEL
GID_INDEXES = {}
HERE = Path(__file__).resolve().parent
TOOLKIT = "/usr/local/Ascend/cann-9.2.T101/set_env.sh"

build.CANDIDATE = REVISION
benchmark.HOSTS = HOSTS
benchmark.REVISION = REVISION
benchmark.ROOT = ROOT
benchmark.HARDWARE = ROOT / "source/dispatch/dispatch-release/tests/hardware"
benchmark.RUNTIME_INSTALL = ROOT / "runtime-install"
benchmark.RUNTIME_REVISION = build.RUNTIME


def read_build(host):
    result = benchmark.ssh(host, f"if test -f {ROOT}/build-result.json; then cat {ROOT}/build-result.json; fi")
    if not result:
        return None
    receipt = json.loads(result)
    assert (receipt["built"], receipt["revision"], receipt["runtime_revision"], receipt["clock_revision"]) == (
        True, REVISION, build.RUNTIME, build.CLOCK)
    return receipt


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
    builds = dict(zip(HOSTS, pool.map(read_build, HOSTS)))
    missing = [host for host, receipt in builds.items() if receipt is None]
    if not missing:
        assert len({receipt["fixture_sha256"] for receipt in builds.values()}) == 1
        return
    with tempfile.TemporaryDirectory(prefix="ubx-two-groups-", dir=Path.cwd()) as temporary:
        bundle = build.prepare(Path(temporary).resolve())
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        builds.update(zip(missing, pool.map(lambda host: build_host(host, bundle, digest), missing)))
    assert len({receipt["fixture_sha256"] for receipt in builds.values()}) == 1


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
        gid = GID_INDEXES.get(HOSTS[index])
        gid_env = f"export TILEXR_RDMA_GID_INDEX={gid} && " if gid is not None else ""
        command = shlex.join(["bash", "-lc",
                              f"source {TOOLKIT} >/dev/null && {gid_env}"
                              f'export LD_LIBRARY_PATH={shlex.quote(lib)}:"${{LD_LIBRARY_PATH:-}}" && exec {command}'])
    rounds = (HERE / "round_count.py").read_text().split('if __name__ == "__main__":', 1)[0]
    remote = (HERE / "run_remote.py").read_text().replace('"90.90.93.188"', f'"{HOSTS[0]}"')
    payload = (rounds + "\n" + remote).encode()
    return json.loads(benchmark.ssh(HOSTS[index], command, payload=payload,
                                    timeout=740 if action == "run" else 215, stage=action))


PORT_CHECK = """
import socket
import sys

base = int(sys.argv[1])
try:
    for port in range(base, base + 514):
        with socket.socket() as probe:
            probe.bind(("0.0.0.0", port))
except OSError:
    print("false")
else:
    print("true")
"""


def select_port(pool, ports):
    for port in ports:
        assert 1024 <= port <= 65535 - 513
        command = shlex.join(["python3", "-", str(port)])
        free = list(pool.map(lambda host: json.loads(
            benchmark.ssh(host, command, payload=PORT_CHECK.encode())), HOSTS))
        if all(free):
            return port
    raise RuntimeError("No common free coordination port range")


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
                       rdma_gid_indexes=GID_INDEXES, route_sha256=route_hash, core_total_samples=32*50*56, local_results=str(result),
                       trace_count=32+int(summary["slowest"]["round"] != 59),
                       last_round=dict(rank=rank, aiv=core, max_us=us, mean_us=statistics.mean(totals)))
        (collection / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        collection.rename(result)
    return summary


def main(args):
    global HOSTS, LABEL, PORT, GID_INDEXES
    HOSTS = tuple(args.get("hosts", DEFAULT_HOSTS))
    assert HOSTS in (DEFAULT_HOSTS, ("90.90.93.83", "90.90.93.86"))
    benchmark.HOSTS = HOSTS
    GID_INDEXES = {host: int(index) for host, index in args.get("gid_indexes", {}).items()}
    assert set(GID_INDEXES) <= set(HOSTS) and all(0 <= index <= 255 for index in GID_INDEXES.values())
    LABEL = DEFAULT_LABEL if HOSTS == DEFAULT_HOSTS else DEFAULT_LABEL + "-83-86"
    PORT = int(args.get("port", 32500))
    benchmark.OUTPUT = ROOT / args.get("run_name", "full-bs32-f50")
    with ThreadPoolExecutor(max_workers=2) as pool:
        if args.get("mode", "initial") == "initial":
            prepare(pool)
        if args.get("mode") != "collect":
            list(pool.map(verify_runtime_receipt, HOSTS))
            PORT = select_port(pool, args.get("ports", [PORT]))
            wait_idle(pool, int(args.get("idle_wait", 240)))
            results = list(pool.map(lambda index: controller(index, "run"), range(2)))
            assert all(result["passed"] and result["route_qps_verified"] == 64 for result in results)
        return collect(pool, Path(args["destination"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    print(json.dumps(main(args), separators=(",", ":")))
