"""Run the two metadata candidates in sequence on the shared CPU SST container."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parent
assert sys.argv[1:2] == ["--args-file"] and len(sys.argv) == 3
args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
mode = args["mode"]
assert mode in {"run", "status"}
results = {}
failed = False
with tempfile.TemporaryDirectory() as directory:
    request = Path(directory) / "request.json"
    for variant in ("forward_page", "range_cache"):
        request.write_text(json.dumps({"mode": mode, "variant": variant}), encoding="utf-8")
        command = [sys.executable, str(root / "run_queue_batch.py"), "--args-file", str(request)]
        proc = subprocess.run(command, capture_output=True, timeout=4100 if mode == "run" else 45)
        try:
            results[variant] = json.loads(proc.stdout)
        except ValueError:
            results[variant] = {"exit_code": proc.returncode, "stderr_bytes": len(proc.stderr)}
        failed |= proc.returncode != 0
print(json.dumps(results, separators=(",", ":")))
raise SystemExit(int(failed))
