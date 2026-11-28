"""Read bounded LR8 qualification and SST diagnostics from the persistent host bind."""

import json
from pathlib import Path
import re
import sys


def read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def mismatch(suite, rank, scenario):
    sys.path.insert(0, str(suite))
    from case_tools import extract_die_output, verified_die_decoder
    if not verified_die_decoder(suite):
        return {}
    record = scenario["ranks"][rank]
    size = scenario["layout"]["bytes_per_rank"]
    offset = record["output_addr"] - record["sys_va_base_addr"] - 64
    dumps = [suite / "bin" / f"david_{rank}_die{die}_outputWithGuards.bin" for die in (1, 2)]
    output = extract_die_output(dumps, "output", offset, size + 128)[64:-64]
    expected = (suite / "golden" / f"rank{rank}.bin").read_bytes()
    first = next((i for i, (actual, golden) in enumerate(zip(output, expected)) if actual != golden), None)
    if first is None:
        return {}
    length = scenario["layout"]["bytes_per_peer"]
    return {"output_offset": first, "source_rank": first // length, "peer_offset": first % length,
            "actual_byte": output[first], "golden_byte": expected[first]}


def main(root, ranks):
    suite = root / f"{ranks}p-suite"
    artifact = read(root / f"{ranks}p-qualified/cmake-artifact.json")
    verified = read(suite / "execution.json")
    scenario = read(suite / "scenario.json")
    runs = sorted((suite / "runs").glob("*/run.json"))
    executed = read(runs[0]) if len(runs) == 1 else {}
    queue = read(suite / "queue_end_state.json")
    counts = {}
    if queue.get("ranks"):
        counts = {key: sum(rank["actual"][key] for rank in queue["ranks"])
                  for key in ("write_wqes", "cqes")}
    result = {"entry": artifact.get("kernel_entry"), "sha256": artifact.get("binary_sha256"),
              "sst_exit": executed.get("returncode"), "runner_exit": verified.get("runner_returncode"),
              "state": verified.get("state"), "simulated_us": verified.get("simulated_us"),
              "validation": {key: executed.get("validation", {}).get(key)
                             for key in ("rank_count", "guards", "kernel_status")},
              "total_queue_counts": counts, "remaining_processes": verified.get("remaining_processes"),
              "run_log": str(runs[0].parent / "sim.log") if runs else None}
    if executed.get("error"):
        error = executed["error"]
        result["failure"] = error[:179]
        match = re.search(r"differs from golden: rank(\d+)", error)
        if match:
            rank = int(match.group(1))
            result["first_reported_rank"] = rank
            try:
                result.update(mismatch(suite, rank, scenario))
            except (ImportError, OSError, ValueError) as exc:
                result["mismatch_decode"] = type(exc).__name__
    elif verified.get("error"):
        result["failure"] = verified["error"][:179]
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]))

