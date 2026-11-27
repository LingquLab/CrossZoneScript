"""Run and analyze the two-host EP32 BS32 route benchmark."""
import concurrent.futures
from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shlex
import statistics
import subprocess
import tarfile
import tempfile
import time

from build import CANDIDATE as REVISION, HOSTS, ROOT as SERVER_ROOT, SSH
from round_count import load_clock_results

ROOT = PurePosixPath(SERVER_ROOT)
HARDWARE = ROOT / "source/dispatch/dispatch-release/tests/hardware"
OUTPUT = ROOT / "full-bs32-f50"
RUNTIME_INSTALL = ROOT / "runtime-install"
RUNTIME_REVISION = None
ROUTE_MODE = "replicate4"
ROUTE_QPS_PER_RANK = {"same_lane": 1, "replicate4": 4, "replicate16": 16}


def ssh(host, command, *, payload=None, timeout=60, stage="SSH"):
    result = subprocess.run(
        [str(SSH), "-o", "ConnectTimeout=12", "-o", "BatchMode=yes",
         "-o", "StrictHostKeyChecking=yes", "root@" + host, command],
        input=payload, capture_output=True, timeout=timeout,
    )
    if result.returncode:
        tail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()[-1:]
        detail = tail[0][:220] if tail else "no stderr"
        raise RuntimeError(f"{host}: {stage} exited {result.returncode}: {detail}")
    return result.stdout


def wait_for_build(pool):
    command = (
        f"if test -f {ROOT}/build-status.txt && test -f {ROOT}/build-result.json; "
        f"then cat {ROOT}/build-status.txt; else printf pending; fi"
    )
    deadline = time.monotonic() + 330
    while time.monotonic() < deadline:
        states = list(pool.map(lambda host: ssh(host, command, timeout=20).decode().strip(), HOSTS))
        for host, state in zip(HOSTS, states):
            if state not in {"pending", "", "complete 0"}:
                raise RuntimeError(f"{host}: prerequisite build status {state}")
        if states == ["complete 0", "complete 0"]:
            return
        time.sleep(10)
    raise TimeoutError("Staged builds did not finish before the benchmark readiness deadline")


def controller(index, action, payload, port, allow_alarm=False):
    argv = shlex.join(["python3", "-", action, str(index), str(port), OUTPUT.name,
                       str(RUNTIME_INSTALL), RUNTIME_REVISION or "-", str(ROOT), REVISION, ROUTE_MODE,
                       "allow-alarm" if allow_alarm else "require-ok"])
    if action == "run":
        libraries = ":".join(str(path) for path in (
            RUNTIME_INSTALL / "lib", RUNTIME_INSTALL / "lib64", ROOT / "dispatch-build",
        ))
        shell = (
            "source /usr/local/Ascend/ascend-toolkit/set_env.sh >/dev/null && "
            f'export LD_LIBRARY_PATH={shlex.quote(libraries)}:"${{LD_LIBRARY_PATH:-}}" && exec {argv}'
        )
        argv = shlex.join(["bash", "-lc", shell])
    timeouts = {"run": 740, "preflight": 215, "preflight-cleanup": 420}
    result = ssh(HOSTS[index], argv, payload=payload, timeout=timeouts[action], stage=action)
    return json.loads(result)


def collect(index, collection):
    members = {
        f"rank{rank}/{name}"
        for rank in range(index * 16, (index + 1) * 16)
        for name in ("core-total-records.json", "clock-models.json", "dispatch.last.trace.json")
    }
    archive = ssh(HOSTS[index], shlex.join(["tar", "-cz", "-C", str(OUTPUT),
                                           "host-result.json", *sorted(members)]), timeout=180)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        entries = bundle.getmembers()
        assert len(entries) == 49 and {entry.name for entry in entries} == members | {"host-result.json"}
        for entry in entries:
            assert entry.isfile()
            relative = f"host{index}-result.json" if entry.name == "host-result.json" else entry.name
            output = collection / relative
            output.parent.mkdir(exist_ok=True)
            with output.open("xb") as handle:
                handle.write(bundle.extractfile(entry).read())
    receipt = json.loads((collection / f"host{index}-result.json").read_text())
    for rank in receipt["ranks"]:
        trace = collection / f"rank{rank['rank']}/dispatch.last.trace.json"
        assert hashlib.sha256(trace.read_bytes()).hexdigest() == rank["trace_sha256"]


def timings(values):
    ordered = sorted(values)
    p90, p95 = (statistics.quantiles(ordered, n=100, method="inclusive")[i - 1] for i in (90, 95))
    return dict(samples=len(ordered), mean_us=statistics.mean(ordered),
                min_us=ordered[0], max_us=ordered[-1], p90_us=p90, p95_us=p95)


def route_load_path(rank, core):
    source_lane = core - 40
    reader_lane = rank % 16
    if ROUTE_MODE == "replicate16":
        return "own_gm"
    if ROUTE_MODE == "replicate4":
        return "own_gm" if source_lane % 4 == reader_lane % 4 else "group_peer_gm"
    if source_lane == reader_lane:
        return "own_gm"
    return "group_peer_gm" if source_lane // 4 == reader_lane // 4 else "cross_group_gm"


def extra_metrics(collection):
    all_rank_rounds = []
    paths = ("own_gm", "group_peer_gm", "cross_group_gm")
    loads = {path: [] for path in paths}
    flag_observations = {path: [] for path in paths}
    routes_ready = {path: [] for path in paths}
    detail_version = {"same_lane": 2, "replicate4": 4, "replicate16": 3}[ROUTE_MODE]
    worst_route_load = None
    for rank in range(32):
        directory = collection / f"rank{rank}"
        rows = json.loads((directory / "core-total-records.json").read_text())
        for row in rows[10:]:
            all_rank_rounds.append((rank, [c["end"] - c["start"] for c in row["cores"]]))
        trace = json.loads((directory / "dispatch.last.trace.json").read_text())
        assert trace["metadata"].get("route_distribution", "same_lane") == ROUTE_MODE
        assert trace["metadata"]["route_sync_detail_version"] == detail_version
        windows = {(e["tid"], e["name"]): e for e in trace["traceEvents"]
                   if e.get("ph") == "X" and 40 <= e["tid"] < 56}
        observed = {e["tid"]: e["args"]["core_entry_us"] for e in trace["traceEvents"]
                    if e.get("ph") == "i" and e["name"] == "route_peer_flag_observed"
                    and 40 <= e["tid"] < 56}
        assert set(observed) == set(range(40, 56))
        for core in range(40, 56):
            path = route_load_path(rank, core)
            load = windows[core, "route_remote_load"]
            sync = windows[core, "route_sync"]
            route_ready = sync["ts"] + sync["dur"] - windows[core, "core_total"]["ts"]
            loads[path].append(load["dur"])
            flag_observations[path].append(observed[core])
            routes_ready[path].append(route_ready)
            if worst_route_load is None or load["dur"] > worst_route_load["us"]:
                worst_route_load = dict(host=HOSTS[rank // 16], rank=rank, aiv=core, path=path, us=load["dur"])
    all_ticks = [value for _, samples in all_rank_rounds for value in samples]
    stats = timings([value / 1000 for value in all_ticks])
    # Compare exact ticks to the 95th percentile represented in hundredths of a tick.
    sorted_ticks = sorted(all_ticks)
    lower, fraction = divmod(95 * (len(sorted_ticks) - 1), 100)
    cutoff = sorted_ticks[lower] * (100 - fraction) + sorted_ticks[lower + 1] * fraction
    rank_rounds = [(rank, max(samples) * 100 > cutoff) for rank, samples in all_rank_rounds]
    return dict(
        core_total=stats,
        rank_rounds_above_core_p95=sum(hit for _, hit in rank_rounds),
        distinct_ranks_above_core_p95=len({rank for rank, hit in rank_rounds if hit}),
        rank_round_count=len(rank_rounds),
        route_remote_load={path: timings(vals) for path, vals in loads.items() if vals},
        route_flag_observed_from_core_entry={path: timings(vals) for path, vals in flag_observations.items() if vals},
        route_ub_ready_from_core_entry={path: timings(vals) for path, vals in routes_ready.items() if vals},
        worst_route_remote_load=worst_route_load,
    )


def summarize(pool, verifier, collection=None):
    workspace = tempfile.TemporaryDirectory(prefix="ubx-replicate4-f50-") if collection is None else nullcontext(collection)
    with workspace as path:
        collection = Path(path)
        list(pool.map(lambda i: collect(i, collection), range(2)))
        verified = verifier.analyze(collection, REVISION, 32, list(HOSTS))
        assert verified["passed"] and verified["profile_mode"] == "full"
        assert verified["stage_details_enabled"] and verified["core_total_samples"] == 89600
        summary = extra_metrics(collection)
    keys = ("revision", "bs", "warmup", "measured", "verified_ranks", "core_total_samples",
            "trace_hashes_verified", "last_trace_round", "group_stats", "round_core_maxima_stats",
            "aligned_entry_spread_stats", "route_sha256", "missed_targets", "physical_hosts_by_logical_group")
    return {
        "passed": True, "route_distribution": ROUTE_MODE,
        "route_qps_verified": 32 * ROUTE_QPS_PER_RANK[ROUTE_MODE],
        "profile_mode": "full", "stage_details_enabled": True, "clock_aligned": True,
        **{key: verified[key] for key in keys}, **summary,
    }


def read_verifier():
    source = ssh(HOSTS[0], shlex.join(["cat", str(HARDWARE / "clock_results.py")])).decode()
    return load_clock_results(source)


def main(ports=(43000,), cleanup=False, collection=None, allow_alarm=False, trace_collector=None):
    here = Path(__file__).resolve().parent
    rounds_script = (here / "round_count.py").read_text().split('if __name__ == "__main__":', 1)[0]
    remote = (here / "run_remote.py").read_text()
    if trace_collector is not None:
        loader = '''    from types import ModuleType
    source = HARDWARE / "run_dispatch_node.py"
    code = source.read_text()
    for old, new in (("== set(range(24, 56))", "== set(range(16, 56))"),
                     ("expected_watchers = set(range(24, 40))", "expected_watchers = set(range(16, 40))")):
        assert code.count(old) == 1
        code = code.replace(old, new)
    run_dispatch_node = ModuleType("run_dispatch_node")
    run_dispatch_node.__file__ = str(source)
    exec(compile(code, str(source), "exec"), run_dispatch_node.__dict__)'''
        assert remote.count("    import run_dispatch_node") == 1
        remote = remote.replace("    import run_dispatch_node", loader)
    payload = (rounds_script + "\n" + remote).encode()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        wait_for_build(pool)
        verifier = read_verifier() if trace_collector is None else None
        action = "preflight-cleanup" if cleanup else "preflight"
        for port in ports:
            try:
                readiness = list(pool.map(lambda i: controller(i, action, payload, port, allow_alarm), range(2)))
                assert all(item["ready"] and item["port"] == port for item in readiness)
                break
            except RuntimeError as exc:
                if "Address already in use" not in str(exc):
                    raise
        else:
            raise RuntimeError("The available coordination port ranges are occupied")
        results = list(pool.map(lambda i: controller(i, "run", payload, port), range(2)))
        assert all(result["passed"] for result in results)
        assert sum(result["route_qps_verified"] for result in results) == 32 * ROUTE_QPS_PER_RANK[ROUTE_MODE]
        summary = (summarize(pool, verifier, collection) if trace_collector is None
                   else trace_collector(pool, collection))
    summary["coordinator_port"] = port
    if allow_alarm:
        summary["preflight_alarms"] = {host: receipt["alarms"] for host, receipt in zip(HOSTS, readiness)}
    if RUNTIME_REVISION:
        summary["runtime_revision"] = RUNTIME_REVISION
        summary["runtime_sha256_by_host"] = {
            host: receipt["runtime_sha256"] for host, receipt in zip(HOSTS, readiness)
        }
    if cleanup:
        summary["npu_cleanup"] = [
            dict(host=host, term_sent=receipt["term_sent"], kill_sent=receipt["kill_sent"])
            for host, receipt in zip(HOSTS, readiness)
        ]
    return summary


if __name__ == "__main__":
    print(json.dumps(main(), separators=(",", ":")))
