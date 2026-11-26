"""Read the disjoint route benchmark receipt or collect verified saved results."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path, PurePosixPath

import benchmark
from retest_disjoint_routes import REVISION, WORK
from shared_mr_status import PROBE

TASK = "replicate4-disjoint-bs32-retest"


def state(host):
    probe = PROBE.replace('runtime = base / "runtime-shared-mr"', 'runtime = base')
    probe = probe.replace('"/root/ubx-replicate4-e172cd2-bs32-f50"', repr(WORK))
    probe = probe.replace('full-bs32-f50-shared-mr', 'full-bs32-f50')
    probe = probe.replace('runtime-result.json', 'build-result.json')
    result = json.loads(benchmark.ssh(host, "python3 -", payload=probe.encode(), timeout=40))
    result["host"] = host
    return result


def main():
    folder = Path.cwd() / f"runs/{TASK}/1/execution"
    receipt = folder / "execution.json"
    saved = json.loads(receipt.read_text(encoding="utf-8")) if receipt.is_file() else {}
    status = saved.get("status", "PENDING")
    exit_code = saved.get("exit_code")
    if status == "EXITED" and exit_code == 0:
        report = json.loads((folder / "stdout.log").read_text(encoding="utf-8"))
        assert report["passed"] and report["revision"] == REVISION
        assert report["core_total_samples"] == 89600 and report["last_trace_round"] == 59
        print(json.dumps(dict(read_only=True, benchmark=report), separators=(",", ":")))
        return
    result = dict(read_only=True, status=status, exit_code=exit_code)
    if status == "EXITED" and exit_code:
        lines = (folder / "stderr.log").read_text(encoding="utf-8", errors="replace").splitlines()
        result["error"] = next((line[:270] for line in reversed(lines) if "Error:" in line), None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        hosts = list(pool.map(state, benchmark.HOSTS))
        result["hosts"] = hosts
        if all(host["archived"] for host in hosts):
            benchmark.ROOT = PurePosixPath(WORK)
            benchmark.HARDWARE = benchmark.ROOT / "source/dispatch/dispatch-release/tests/hardware"
            benchmark.OUTPUT = benchmark.ROOT / "full-bs32-f50"
            benchmark.REVISION = REVISION
            result["recovered_benchmark"] = benchmark.summarize(pool, benchmark.read_verifier())
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
