# Copyright (c) 2026 TileXR Project.
"""Inspect the previous session's retained simulator evidence over SSH.

The remote probe reads the named container and candidate without starting a run.
Full subprocess output stays in this Bridge task's local capture file.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def main(args):
    assert args["ssh_target"] == "root@141.61.94.26"
    assert args["previous_session"] == "cb41ec7d-5530-4f29-981d-6274e209a40e"
    key = Path(os.environ["USERPROFILE"]) / ".ssh/id_ed25519"
    command = ["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
               "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", args["ssh_target"],
               shlex.join(["python3", "-", json.dumps(args)])]
    probe = Path(__file__).with_name("recover_host.py").read_bytes()
    result = subprocess.run(command, input=probe, capture_output=True, timeout=100)
    Path("send-recover.log").write_bytes(result.stdout + b"\nSTDERR\n" + result.stderr)
    if result.returncode:
        sys.stderr.buffer.write(result.stderr[-6000:])
    assert result.returncode == 0, "remote_probe_failed"
    value = json.loads(result.stdout)
    value["ssh_diagnostic_bytes"] = len(result.stderr)
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    assert len(encoded.encode()) <= 12000
    assert value["identity_ok"] and value["object_matches_previous_result"]
    print(encoded)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    try:
        main(json.loads(parser.parse_args().args_file.read_text(encoding="utf-8")))
    except Exception as error:
        print(json.dumps({"probe_ok": False, "error": type(error).__name__}))
        sys.exit(1)
