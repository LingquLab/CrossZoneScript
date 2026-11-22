"""Read retained HT metadata, schedule and reducer readiness receipts."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parent
results = {}
with tempfile.TemporaryDirectory() as directory:
    request = Path(directory) / "request.json"
    for batch in ("metadata", "schedule", "ready", "integration"):
        options = {"mode": "status"}
        if batch == "integration":
            options["combined"] = True
        request.write_text(json.dumps(options), encoding="ascii")
        runner_name = "ready" if batch == "integration" else batch
        runner = str(root / f"run_{runner_name}_ab.py")
        proc = subprocess.run([sys.executable, runner, "--args-file", str(request)],
                              capture_output=True, timeout=100)
        try:
            results[batch] = json.loads(proc.stdout)
        except ValueError:
            results[batch] = {"exit_code": proc.returncode, "stderr_bytes": len(proc.stderr)}
print(json.dumps(results, separators=(",", ":")))
