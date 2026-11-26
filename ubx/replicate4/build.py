"""Build the pinned EP32 BS32 candidate on both Ascend hosts."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess

HOSTS = ("90.90.93.188", "90.90.93.84")
SSH = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/OpenSSH/ssh.exe"


def build(host, script):
    result = subprocess.run(
        [str(SSH), "-o", "ConnectTimeout=12", "-o", "BatchMode=yes",
         "-o", "StrictHostKeyChecking=yes", "root@" + host, "bash -s"],
        input=script, capture_output=True, timeout=1500,
    )
    if result.returncode:
        raise RuntimeError(f"{host}: remote build exited {result.returncode}")
    receipt = json.loads(result.stdout)
    if not receipt["built"]:
        raise RuntimeError(f"{host}: missing successful build receipt")
    return {"host": host, **receipt}


def main():
    script = Path(__file__).with_suffix(".sh").read_bytes().replace(b"\r\n", b"\n")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda host: build(host, script), HOSTS))
    print(json.dumps({
        "built": True, "builds": results,
        "kernel_hashes_match": (
            results[0]["artifacts"]["ubx_dispatch_ep32.o"] ==
            results[1]["artifacts"]["ubx_dispatch_ep32.o"]
        ),
        "npu_workload_started": False,
    }, separators=(",", ":")))


if __name__ == "__main__":
    main()
