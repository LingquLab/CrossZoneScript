"""Check whether the EP32 hosts remain reachable after the authorized hot reset."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import time

from benchmark import HOSTS, ssh

PEER = r"""
import json
import socket
import subprocess

target = "90.90.93.84"
def tcp():
    try:
        with socket.create_connection((target, 22), timeout=4):
            return "open"
    except OSError as error:
        return type(error).__name__

result = subprocess.run(["ping", "-c", "2", "-W", "2", target], capture_output=True, timeout=8)
print(json.dumps(dict(ssh_tcp=tcp(), ping_exit=result.returncode)))
"""


def listener(host):
    try:
        with socket.create_connection((host, 22), timeout=4):
            return "open"
    except OSError as error:
        return type(error).__name__


def observe():
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = dict(zip(HOSTS, pool.map(listener, HOSTS)))
    return dict(checked_utc=datetime.now(timezone.utc).isoformat(), green_ssh_tcp=result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    observations = []
    for index in range(4):
        result = observe()
        observations.append(result)
        if all(value == "open" for value in result["green_ssh_tcp"].values()):
            break
        if index != 3:
            time.sleep(12)
    output = Path(args["destination"]) / "replicate4-local-receive-bs32-093dbcd-diagnostics/npu-reset-connectivity.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        peer = json.loads(ssh(HOSTS[0], "python3 -", payload=PEER.encode(), timeout=28, stage="peer reachability"))
    except (RuntimeError, ValueError) as error:
        peer = {"error": str(error)[:200]}
    report = dict(observations=observations, from_188_to_84=peer, local_diagnostics=str(output))
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, separators=(",", ":")))
