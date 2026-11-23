"""Collect a bounded adaptation audit over the existing green SSH identity."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def main(args):
    assert args == {"ssh_target": "root@141.61.94.26", "hostname": "host-94-26"}
    command = ["ssh", "-T", "-i", str(Path(os.environ["USERPROFILE"]) / ".ssh/id_ed25519"),
               "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "ConnectTimeout=10", args["ssh_target"],
               shlex.join(["python3", "-", args["hostname"]])]
    result = subprocess.run(command, input=Path(__file__).with_name("audit_adaptations_host.py").read_bytes(),
                            capture_output=True, timeout=75)
    Path("adaptation-audit.log").write_bytes(result.stdout + b"\nSTDERR\n" + result.stderr)
    assert result.returncode == 0, "adaptation_audit_failed"
    value = json.loads(result.stdout)
    assert len(value["cases"]) == 3 and not value["simulation_started"]
    packet = json.dumps(value, separators=(",", ":"))
    assert len(packet.encode()) < 12000
    print(packet)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    try:
        main(json.loads(parser.parse_args().args_file.read_text(encoding="utf-8")))
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error)[:100]}))
        sys.exit(1)
