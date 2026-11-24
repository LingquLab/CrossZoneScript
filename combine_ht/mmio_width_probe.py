"""Run bounded one-WQE width diagnostics in the existing idle isolated container."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    assert args in ({}, {"mte": True}, {"unbonded": True})
    experiment = "mmio-mte-unbonded-a1" if args.get("unbonded") else "mmio-mte-a1" if args.get("mte") else "mmio-width-a1"
    key = Path(os.environ["USERPROFILE"]) / ".ssh/id_ed25519"
    ssh = ["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
           "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12", "root@141.61.94.26"]
    def remote(argv, payload=None, timeout=30):
        p = subprocess.run(ssh + [shlex.join(argv)], input=payload, capture_output=True, timeout=timeout)
        return p
    p = remote(["docker", "inspect", "tilexr-combine-ht-f71d0252-v11"])
    assert p.returncode == 0
    identity = json.loads(p.stdout)[0]
    assert identity["Id"] == "0686f95f26f3e703650724ee7e6da7293b11f7e4ce7154d80e6a089a6614d4c9"
    assert identity["HostConfig"]["NetworkMode"] == "none"
    p = remote(["docker", "top", "tilexr-combine-ht-f71d0252-v11", "-eo", "pid,comm"])
    assert p.returncode == 0 and not any(line.split()[-1] in ("sst", "sstsim.x") for line in p.stdout.decode().splitlines()[1:])
    work = "/var/tmp/tilexr-combine-ht-f71d0252-ef1e-4b5c-b299-c2429ebcfbdf-v11"
    directory = work + "/diagnostics/" + experiment
    assert remote(["mkdir", "-m", "700", directory]).returncode == 0
    for filename in ("mmio_width_probe.cpp", "mmio_width_container.py"):
        assert remote(["bash", "-c", 'umask 077; set -C; cat > "$1"', "upload", directory + "/" + filename],
                      Path(__file__).with_name(filename).read_bytes()).returncode == 0
    assert remote(["bash", "-c", 'set -e; umask 077; cat > "$1.tmp"; mv "$1.tmp" "$1"', "upload", work + "/sst.py"],
                  Path(__file__).with_name("sst.py").read_bytes()).returncode == 0
    p = remote(["docker", "exec", "tilexr-combine-ht-f71d0252-v11", "python3",
                "/work/diagnostics/" + experiment + "/mmio_width_container.py"], timeout=1500)
    if p.stdout:
        print(json.dumps(json.loads(p.stdout)))
    return 0 if p.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
