"""Inspect stored outputs without replaying the simulator."""
from collections import Counter
import json
from pathlib import Path
import re
import struct

MOUNT = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native")


def diagnose(name):
    case = MOUNT / name
    if not (case / "run.json").is_file():
        return {"case_exists": case.is_dir()}
    run = json.loads((case / "run.json").read_text())
    result = {"run": {k: run[k] for k in ("status", "returncode", "error") if k in run}}
    log = case / "check.log"
    if log.is_file():
        result["checker_error"] = [line[:180] for line in log.read_text(errors="replace").splitlines()
                                   if line.startswith(("ValueError:", "AssertionError:", "RuntimeError:"))][-2:]
    outputs = case / "actual"
    if outputs.is_dir():
        checks = Counter()
        states = Counter()
        affected = Counter()
        for rank in range(64):
            actual = outputs / f"rank{rank:02d}"
            fixture = case / "fixture-data" / actual.name
            if not actual.is_dir():
                continue
            checks["ranks"] += 1
            for owner, address in enumerate(range(0, 512, 32)):
                status, = struct.unpack_from("<I", (actual / "status.bin").read_bytes(), address)
                states[status] += 1
                if status:
                    affected[owner] += 1
            for item in ("returned", "completed"):
                actual_bytes = (actual / (item + ".bin")).read_bytes()
                expected = (fixture / ("golden-" + item + ".bin")).read_bytes()
                if item == "returned":
                    checks["returned_ok"] += actual_bytes == expected
                else:
                    checks["completion_ok"] += all(actual_bytes[i:i+8] == expected[i:i+8]
                                                   for i in range(0, len(expected), 512))
        result.update(checks=checks, status=states, affected_owners=affected)
    sim_log = case / "sim.log"
    if sim_log.is_file():
        data = sim_log.read_bytes()
        result["cqe_log_records"] = data.count(b"WrCQEResp")
        result["sim_errors"] = sum(bool(re.search(rb"\b(?:fatal|assertion failed|device exception)\b", line, re.I))
                                   for line in data.splitlines())
    return result
