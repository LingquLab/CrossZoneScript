"""Read the disjoint route benchmark receipt or collect verified saved results."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path, PurePosixPath

import benchmark
from retest_disjoint_routes import REVISION, WORK
from shared_mr_status import PROBE

TASK = "replicate4-disjoint-bs32-retest"


def state(host, work):
    probe = PROBE.replace('runtime = base / "runtime-shared-mr"', 'runtime = base')
    probe = probe.replace('"/root/ubx-replicate4-e172cd2-bs32-f50"', repr(work))
    probe = probe.replace('full-bs32-f50-shared-mr', 'full-bs32-f50')
    probe = probe.replace('runtime-result.json', 'build-result.json')
    result = json.loads(benchmark.ssh(host, "python3 -", payload=probe.encode(), timeout=40))
    result["host"] = host
    return result


def main(task=TASK, revision=REVISION, work=WORK, mode="replicate4", iteration=1, destination=None):
    folder = Path.cwd() / f"runs/{task}/{iteration}/execution"
    receipt = folder / "execution.json"
    saved = json.loads(receipt.read_text(encoding="utf-8")) if receipt.is_file() else {}
    status = saved.get("status", "PENDING")
    exit_code = saved.get("exit_code")
    if status == "EXITED" and exit_code == 0:
        report = json.loads((folder / "stdout.log").read_text(encoding="utf-8"))
        assert report["passed"] and report["revision"] == revision
        assert report["route_distribution"] == mode
        assert report["route_qps_verified"] == 32 * benchmark.ROUTE_QPS_PER_RANK[mode]
        assert report["core_total_samples"] == 89600 and report["last_trace_round"] == 59
        print(json.dumps(dict(read_only=True, benchmark=report), separators=(",", ":")))
        return
    result = dict(read_only=True, status=status, exit_code=exit_code)
    if status == "EXITED" and exit_code:
        lines = (folder / "stderr.log").read_text(encoding="utf-8", errors="replace").splitlines()
        result["error"] = next((line[:270] for line in reversed(lines) if "Error:" in line), None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        hosts = list(pool.map(lambda host: state(host, work), benchmark.HOSTS))
        result["hosts"] = hosts
        if all(host["archived"] for host in hosts):
            benchmark.ROOT = PurePosixPath(work)
            benchmark.HARDWARE = benchmark.ROOT / "source/dispatch/dispatch-release/tests/hardware"
            benchmark.OUTPUT = benchmark.ROOT / "full-bs32-f50"
            benchmark.REVISION = revision
            benchmark.ROUTE_MODE = mode
            result["recovered_benchmark"] = benchmark.summarize(pool, benchmark.read_verifier())
    if destination is not None:
        destination.mkdir(parents=True, exist_ok=True)
        output = destination / f"attempt-{iteration:02d}.json"
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        result["local_diagnostics"] = str(output)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
