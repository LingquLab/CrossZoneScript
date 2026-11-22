"""Export the completed BS64 and BS128 A/B/C timing comparison at maxTokens=2."""

import json
from pathlib import Path, PurePosixPath
import shlex
import sys

from dispatch_abc_setup import NAMESPACE
from stage_sources import connect, run_remote

COMPARE = r'''
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
state = json.loads((root / "report-status.json").read_text())
records, missing = [], []
for batch in (64, 128):
    for mode in ("off", "on"):
        for label in ("a", "b", "c"):
            name = f"tail-{label}-dispatch-bs{batch}-max2-{mode}"
            path = root / "runs" / name / "timing.json"
            if not path.is_file():
                missing.append(name)
                continue
            timing = json.loads(path.read_text())
            if (timing["case"] != name or timing["samples"] != 640 or
                    timing["max_tokens"] != 2 or timing["effective_max_tokens"] != 2 or not timing["native"]):
                raise ValueError("unexpected A/B/C timing provenance")
            native = timing["native_task_us"]["round_slowest_rank"]
            phases = {
                f"{item['role']}/{item['stage']}": round(item["round_slowest_core_us"]["median"], 3)
                for item in timing.get("stages", [])
                if item["stage"] in {"topk_submit", "topk_forward_submit", "expert_ready_wait", "topk_exchange"}
            }
            records.append(dict(bs=batch, mode=mode, variant=label.upper(),
                                us=[round(native[field], 4) for field in ("min", "median", "max")],
                                backend=timing["backend"], abi=timing["abi"],
                                rdma_bytes=timing["rdma_bytes"]["median"],
                                rdma_GBps=round(timing["rdma_GBps"]["median"], 3), phases=phases))
print(json.dumps(dict(rows=records, missing=missing, reports=state["reports"]), separators=(",", ":")))
'''

if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    with connect(args["host"]) as client, client.open_sftp() as transfer:
        directory = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / NAMESPACE
        status, output, _ = run_remote(client, "/usr/bin/python3 - " + shlex.quote(str(directory)),
                                       input_text=COMPARE)
        if status:
            raise SystemExit(status)
        print(output)
