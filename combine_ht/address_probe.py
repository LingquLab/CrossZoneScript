"""Run one contained V1.1.0 address probe in the continued task's idle container."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

WORK = "/var/tmp/tilexr-combine-ht-f71d0252-ef1e-4b5c-b299-c2429ebcfbdf-v11"
CONTAINER = "tilexr-combine-ht-f71d0252-v11"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    assert json.loads(parser.parse_args().args_file.read_text(encoding="utf-8")) == {}
    key = Path(os.environ["USERPROFILE"]) / ".ssh/id_ed25519"
    ssh = ["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
           "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12", "root@141.61.94.26"]

    def remote(argv, payload=None, timeout=30):
        p = subprocess.run(ssh + [shlex.join(argv)], input=payload, capture_output=True, timeout=timeout)
        assert p.returncode == 0, "probe_transport_exit_" + str(p.returncode)
        return p.stdout

    identity = json.loads(remote(["docker", "inspect", CONTAINER]))[0]
    assert identity["Image"] == "sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f", "unexpected_image"
    assert identity["State"]["Running"], "container_not_running"
    assert identity["HostConfig"]["NetworkMode"] == "none", "unexpected_network_mode"
    assert identity["Config"].get("Labels", {}).get("crosszone.session") == "f71d0252-ef1e-4b5c-b299-c2429ebcfbdf", "unexpected_session_label"
    top = remote(["docker", "top", CONTAINER, "-eo", "pid,comm"]).decode().splitlines()[1:]
    assert not any(line.split()[-1] in ("sst", "sstsim.x") for line in top), "sst_already_active"
    directory = WORK + "/diagnostics/stdev-address-a1"
    remote(["mkdir", "-m", "700", directory])
    for name in ("address_probe.cpp", "address_probe_container.py"):
        remote(["bash", "-c", 'umask 077; set -C; cat > "$1"', "upload", directory + "/" + name],
               Path(__file__).with_name(name).read_bytes())
    result = json.loads(remote(["docker", "exec", CONTAINER, "python3",
                               "/work/diagnostics/stdev-address-a1/address_probe_container.py"], timeout=330))
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    try:
        code = main()
    except Exception as error:
        print(json.dumps(dict(ok=False, error_type=type(error).__name__, detail=str(error)[:240])))
        code = 1
    sys.exit(code)
