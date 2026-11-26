"""Resume only if the previous benchmark exited before NPU preflight."""
import json
from pathlib import Path
import time

import benchmark


def main():
    receipt = Path.cwd() / "runs/replicate4-bs32-50-benchmark/1/execution"
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        record_path = receipt / "execution.json"
        if record_path.is_file():
            record = json.loads(record_path.read_text())
            if record["status"] == "EXITED":
                break
            if record["status"] not in {"STARTING", "RUNNING"}:
                raise RuntimeError(f"Prior benchmark receipt requires inspection: {record['status']}")
        time.sleep(5)
    else:
        raise TimeoutError("Prior benchmark did not return a terminal receipt")

    if record["exit_code"] == 0:
        output = json.loads((receipt / "stdout.log").read_text(encoding="utf-8"))
        assert output["passed"] and output["revision"] == benchmark.REVISION
        assert output["core_total_samples"] == 89600 and output["last_trace_round"] == 59
        print(json.dumps(output, separators=(",", ":")))
        return

    stderr = (receipt / "stderr.log").read_text(encoding="utf-8", errors="replace")
    if ("prerequisite build status build-dispatch 1" not in stderr
        and "Staged builds did not finish before the benchmark readiness deadline" not in stderr):
        raise RuntimeError("Prior failure occurred outside the pre-launch build gate")
    benchmark.main()


if __name__ == "__main__":
    main()
