"""Read the failed case and its existing V1.1.0 libraries without another SST run."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    assert args in ({}, {"memory_attributes": True}, {"routing_logic": True}, {"store_interfaces": True}, {"write_response": True})
    key = Path(os.environ["USERPROFILE"]) / ".ssh/id_ed25519"
    command = ["docker", "exec", "-i", "tilexr-combine-ht-f71d0252-v11", "python3", "-"]
    argv = ["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12", "root@141.61.94.26", shlex.join(command)]
    entry = ("write_response_container.py" if args.get("write_response") else
             "store_interfaces_container.py" if args.get("store_interfaces") else
             "routing_logic_container.py" if args.get("routing_logic") else
             "memory_attributes_container.py" if args.get("memory_attributes") else "mapping_probe_container.py")
    p = subprocess.run(argv, input=Path(__file__).with_name(entry).read_bytes(),
                       capture_output=True, timeout=240)
    assert p.returncode == 0, "mapping_query_exit_" + str(p.returncode)
    print(json.dumps(json.loads(p.stdout)))


if __name__ == "__main__":
    main()
