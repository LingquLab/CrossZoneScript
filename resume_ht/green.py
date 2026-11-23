# Copyright (c) 2026 TileXR Project.
"""Run this session's bounded read-only SST inspection through the approved SSH key."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

SESSION = "125e2ae2-ec25-4a56-a2c4-acef9e82da84"


def main(args):
    assert args == {"action": "inspect-existing"}
    local = Path(os.environ["LOCALAPPDATA"]) / "CrossZone/tilexr" / SESSION
    local.mkdir(parents=True, exist_ok=True)
    key = Path(os.environ["USERPROFILE"]) / ".ssh/id_ed25519"
    argv = ["ssh", "-T", "-i", str(key), "-p", "22", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12", "root@141.61.94.26",
            "timeout --kill-after=5 90 python3 -"]
    proc = subprocess.run(argv, input=Path(__file__).with_name("inspect_host.py").read_bytes(),
                          capture_output=True, timeout=110)
    (local / "inspect.stdout").write_bytes(proc.stdout)
    (local / "inspect.stderr").write_bytes(proc.stderr)
    assert proc.returncode == 0, "inspection_failed_see_green_local_log"
    result = json.loads(proc.stdout)
    assert result["ok"] and result["hostname"] == "host-94-26"
    assert len(proc.stdout) < 50000
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    try:
        sys.exit(main(args))
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps(dict(ok=False, error_type=type(error).__name__, detail="see_green_local_log")))
        sys.exit(1)
