"""Deploy the pinned harness, confirm profiled send, and run the independent sweeps."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

import matrix


def benchmark(config, mode, phase):
    try:
        return matrix.run(dict(config, mode=mode, phase=phase))
    except subprocess.TimeoutExpired:
        print(json.dumps({"stage": "deadline_expired", "mode": mode, "phase": phase}), flush=True)
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    config = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    build = subprocess.run([
        sys.executable, str(Path(__file__).with_name("run.py")), "--host", config["host"], "--ref", config["ref"],
        "--revision", config["revision"], "--build-only",
    ], timeout=1800)
    if build.returncode:
        print(json.dumps({"stage": "campaign_build", "exit": build.returncode}), flush=True)
        return
    send_ready = benchmark(config, "send", "scale")
    reduce_passed = benchmark(config, "reduce", "matrix")
    send_passed = send_ready and benchmark(config, "send", "matrix")
    print(json.dumps({"stage": "campaign_finished", "send_calibration_passed": send_ready,
                      "reduce_matrix_passed": reduce_passed, "send_matrix_passed": send_passed}))


if __name__ == "__main__":
    try:
        main()
    except subprocess.TimeoutExpired:
        print(json.dumps({"stage": "candidate_build", "error": "deadline_expired"}))
