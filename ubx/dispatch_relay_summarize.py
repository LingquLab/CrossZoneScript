"""Read completed RELAY/INTRA timings and stage comparisons without repackaging."""

import json
from pathlib import Path, PurePosixPath
import shlex
import sys

from dispatch_relay_setup import benchmark_revision
from stage_sources import connect, run_remote

SUMMARY = r'''
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
revision = sys.argv[2]
matrix = json.loads((root / "matrix.json").read_text())
status = json.loads((root / "status.json").read_text())
assert status["phase"] == "completed" and status["completed"] == len(matrix) == 24
metrics = {
    ("RELAY", "topk_relay_plan"): "relay_plan",
    ("RELAY", "relay_compute"): "relay_compute",
    ("RELAY", "relay_flag_to_mem_send_end"): "relay_flag_to_submit",
    ("INTRA_SEND", "intra_send_role"): "intra_send",
}
native, stages = {}, {}
for variant, operator, batch, mode, tokens in matrix:
    case = f"{variant}-{operator}-bs{batch}-max{tokens}-{mode}"
    doc = json.loads((root / "runs" / case / "timing.json").read_text())
    assert doc["native"] and doc["samples"] == 640 and tokens == 2 and doc["max_tokens"] == tokens
    assert "revision=" + revision in doc["provenance"]
    key = f"{batch}-{variant.rsplit('-', 1)[-1]}"
    stats = doc["native_task_us"]["round_slowest_rank"]
    native[f"{key}-{mode}"] = [round(stats[field], 4) for field in ("min", "median", "max")]
    if mode == "on":
        stages[key] = {
            metrics[row["role"], row["stage"]]: round(row["round_slowest_core_us"]["median"], 4)
            for row in doc["stages"] if (row["role"], row["stage"]) in metrics
        }
target = root / f"TileXR-UBX-EP32-RELAY-{revision[:8]}.zip"
print(json.dumps(dict(native_us=native, median_stage_us=stages,
                      archive_size=target.stat().st_size if target.is_file() else None),
                 separators=(",", ":")))
'''

if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    revision, namespace, _ = benchmark_revision(args["revision"])
    with connect(args["host"]) as client, client.open_sftp() as transfer:
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / namespace
        command = "/usr/bin/python3 - " + shlex.quote(str(root)) + " " + shlex.quote(revision)
        code, output, _ = run_remote(client, command, timeout=30, input_text=SUMMARY)
        if code:
            raise SystemExit(code)
        print(output)
