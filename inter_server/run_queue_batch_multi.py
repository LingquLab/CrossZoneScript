"""Validate multi-chunk EP64 with the same prepared images for both objects."""
import argparse
import base64
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--args-file", type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
root = Path(__file__).resolve().parent
resource = root / "resources"
mode = args["mode"]
variant = args.get("variant", "queue_batch")
assert mode in {"run", "status", "matrix"} and variant in {"queue_batch", "hotpath", "batch_cqe"}
build = json.loads((resource / variant / "build-record.json").read_text())
request = {"mode": mode, "build": build, "variant": variant, "memory_retry": bool(args.get("memory_retry"))}
if mode in {"run", "matrix"}:
    request["identity"] = json.loads((resource / "queue_batch_multi/fixture-identity.json").read_text())
    request["archive"] = base64.b64encode((resource / "queue_batch_multi/fixture-images.tar.gz").read_bytes()).decode("ascii")
    request["object"] = base64.b64encode((resource / variant / "inter_server_send.o.gz").read_bytes()).decode("ascii")
if mode == "matrix":
    request["variants"] = {}
    for tag in ("hotpath", "queue_batch", "batch_cqe"):
        request["variants"][tag] = {
            "build": json.loads((resource / tag / "build-record.json").read_text()),
            "object": base64.b64encode((resource / tag / "inter_server_send.o.gz").read_bytes()).decode("ascii"),
        }
runner = (root / "run_mte_t4_remote.py").read_text().rsplit("if __name__ == ", 1)[0]
runner = runner.replace('assert tokens == 4','assert tokens in (4, 257)')
runner = runner.replace('"hidden": 256','"hidden": request.get("hidden", 256)')
logic = (root / "run_queue_batch_remote.py").read_text() + '\n' + (root / "run_queue_batch_multi_remote.py").read_text()
script = runner + "\n" + logic + "\nprint(json.dumps(run_multichunk(" + repr(request) + ")))\n"
compile(script, "<multi_chunk_runner>", "exec")
command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"]
p = subprocess.run(command, input=script.encode("utf-8"), capture_output=True,
                   timeout={"run": 2120, "status": 45, "matrix": 8300}[mode])
if p.returncode:
    print(json.dumps({"remote_exit_code": p.returncode, "stderr_bytes": len(p.stderr)}))
    raise SystemExit(1)
row = json.loads(p.stdout)
if mode in {"run", "matrix"}:
    keys = ("candidate_commit", "kernel_sha256", "runner_exit_code", "runner_wall_seconds",
            "sst_simulated_microseconds", "exported_rank_count", "run", "container_stopped", "runner_exception_type")
    if mode == "matrix":
        print(json.dumps({tag: {key: record[key] for key in keys if key in record}
                          for tag, record in row.items()}, separators=(",", ":")))
        if len(row) != 3 or any(record.get("run", {}).get("status") != "sst_and_checker_passed"
                               for record in row.values()):
            raise SystemExit(1)
    else:
        print(json.dumps({key: row[key] for key in keys if key in row}, separators=(",", ":")))
        if row.get("run", {}).get("status") != "sst_and_checker_passed":
            raise SystemExit(1)
else:
    print(json.dumps(row, separators=(",", ":")))
