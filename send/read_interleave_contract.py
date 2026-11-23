"""Read retained target-version SIMD contracts without starting a simulation."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def main(args):
    assert args == {"ssh_target": "root@141.61.94.26", "hostname": "host-94-26"}
    key = Path(os.environ["USERPROFILE"]) / ".ssh/id_ed25519"
    command = ["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes", "-o",
               "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes", "-o",
               "ConnectTimeout=10", args["ssh_target"],
               shlex.join(["python3", "-", args["hostname"]])]
    result = subprocess.run(command, input=Path(__file__).with_name("interleave_contract_host.py").read_bytes(),
                            capture_output=True, timeout=75)
    Path("interleave-contract-capture.log").write_bytes(result.stdout + b"\nSTDERR\n" + result.stderr)
    assert result.returncode == 0, "read_only_contract_query_failed"
    value = json.loads(result.stdout)
    assert value["contract_functions"], "missing_contract"
    packet = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    assert len(packet.encode()) < 14000
    print(packet)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    try:
        main(json.loads(parser.parse_args().args_file.read_text(encoding="utf-8")))
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error)[:100]}))
        sys.exit(1)
