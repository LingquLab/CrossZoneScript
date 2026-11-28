"""Summarize existing acceptance receipts on the green workstation."""

import argparse
import json
from pathlib import Path
import re


RESULTS = Path("D:/workspace/TileXR-build-results")
LR8 = {
    "sim-split0": "alltoall-sst-lr8-sim-split0-75rpqk3j",
    "sim-split1": "alltoall-sst-lr8-sim-split1-73bqm484",
}
MEMORY = {
    "baseline": "alltoall-sst-memory-baseline-3ac88i9z",
    "candidate": "alltoall-sst-memory-candidate-j_phfdts",
}


def load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def lr8_receipt(side):
    stage = RESULTS / LR8[side] / "8p"
    markers = {
        "permission_denied": r"Permission denied",
        "payload_failed": r"golden.*(?:mismatch|failed)|(?:payload|output).*mismatch",
        "guard_failed": r"guard.*(?:mismatch|failed)",
        "kernel_status_failed": r"kernel status.*(?:mismatch|failed)",
        "timeout": r"timed out|TimeoutExpired",
        "prepared": "CURRENT_KERNEL_SUITE_PREPARED_NOT_SIMULATED",
        "verified": "CURRENT_KERNEL_SST_OUTPUTS_VERIFIED",
    }
    transport = {}
    for path in (stage / "unitest").rglob("transport.json"):
        record, = load(path)
        output = record["stdout"] + record["stderr"]
        category = [name for name, pattern in markers.items() if re.search(pattern, output, re.I)]
        error_types = re.findall(r"^([A-Za-z]+Error|TimeoutExpired):", record["stderr"], re.M)
        transport[path.parent.name] = dict(exit=record["returncode"], categories=category,
                                          error=error_types[-1] if error_types else None)
    return dict(side=side, transport=transport)


def memory_receipt(side):
    path = RESULTS / MEMORY[side] / "result.log"
    data = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    return dict(side=side, cases={name.removeprefix("memory_"): [row[key] for key in
                ("rank_count", "device_state", "object_sha256", "ranks_sha256")] for name, row in data["cases"].items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--args-file", required=True, type=Path)
    side = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))["side"]
    result = lr8_receipt(side) if side in LR8 else memory_receipt(side)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
