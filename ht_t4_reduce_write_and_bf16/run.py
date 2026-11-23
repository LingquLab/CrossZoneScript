"""Run the two T4/H16 ready archives through the existing SST runner."""
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
results = {}
for label, subdirectory in (("fp32_write", "ht_t4_reduce_write"), ("native_bf16", "ht_t4_reduce_native_bf16")):
    try:
        execution = subprocess.run(
            [sys.executable, str(root / subdirectory / "run.py")],
            capture_output=True, timeout=330,
        )
    except subprocess.TimeoutExpired:
        results[label] = {"runner_timeout": True}
        continue
    try:
        results[label] = json.loads(execution.stdout)["opt"] if execution.returncode == 0 else {
            "runner_exit": execution.returncode, "stderr_bytes": len(execution.stderr),
        }
    except (ValueError, KeyError):
        results[label] = {"runner_output_invalid": True, "stderr_bytes": len(execution.stderr)}
print(json.dumps(results, separators=(",", ":")))
