"""Project validation records and identities from one Memory SST bind."""

import hashlib
import json
from pathlib import Path
import sys


def read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main():
    root = Path(sys.argv[1])
    qual = read(root / "receipts/qualification.json")
    kernels = qual.get("kernels", {})
    if sys.argv[2] == "build":
        objects = {name.removeprefix("memory_"): row.get("sha256") for name, row in kernels.items()}
        print(json.dumps({"count": len(objects), "kernel_set_sha256": hashlib.sha256(
            json.dumps(objects, sort_keys=True).encode()).hexdigest(), "objects": objects}, separators=(",", ":")))
        return
    cases = {}
    for path in sorted((root / "results").glob("memory_*")):
        case = path.name
        run = path / "run"
        process = read(run / "process.json")
        result = read(run / "result.json")
        controller = read(path / "result.json")
        ranks = read(run / "ranks.json")
        expected_words = [[2] * 8] + [[1] * 8 for _ in range(7)]
        cases[case.removeprefix("memory_")] = {
            "controller": controller.get("status"), "state": result.get("state"),
            "runner": process.get("runner_returncode"), "remaining": process.get("remaining_processes"),
            "ranks": len(ranks), "complete_ranks": [row["rank"] for row in ranks] == list(range(len(ranks))) and bool(ranks),
            "normal_status": sum(row["kernel_status"] == "passed" for row in ranks),
            "negative_status": sum(row["kernel_status_words"] == expected_words for row in ranks),
            "golden_guard_padding": sum(all(row[check] == "passed" for check in ("payload", "guards", "padding")) for row in ranks),
            "first_status": ranks[0]["kernel_status_words"] if ranks and "diagnostic-" in root.name else None,
            "qualified_elf": kernels.get(case, {}).get("sha256"),
            "suite_elf": sha256(root / "suites" / case / "kernel.o"),
        }
    print(json.dumps({"revision": qual.get("revision"), "cases": cases}, separators=(",", ":")))


if __name__ == "__main__":
    main()
