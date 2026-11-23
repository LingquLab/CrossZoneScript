# Copyright (c) 2026 TileXR Project.
"""Read retained synthetic SST results and container state without launching work."""

import hashlib
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess

OLD_SESSION = "500e2a93-b84e-4d36-a9aa-8d2cd0321cdf"
WORK = Path("/var/tmp/tilexr-combine-ht-" + OLD_SESSION + "-v11")
NAME = "tilexr-combine-ht-" + OLD_SESSION[:8] + "-v11"
IMAGE = "sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f"


def call(*argv):
    proc = subprocess.run(argv, capture_output=True, timeout=30, text=True)
    assert proc.returncode == 0, "read_command_failed"
    return proc.stdout


def main():
    assert socket.gethostname().split(".")[0] == "host-94-26"
    info = json.loads(call("docker", "inspect", "--format", "{{json .}}", NAME))
    assert info["Image"] == IMAGE
    assert info["Config"]["Labels"].get("crosszone.session") == OLD_SESSION
    assert any(m["Source"] == str(WORK) and m["Destination"] == "/work" for m in info["Mounts"])
    top = call("docker", "top", NAME, "-eo", "pid,comm,stat,etime") if info["State"]["Running"] else ""
    processes = [line.split() for line in top.splitlines()[1:]]
    result = dict(ok=True, read_only=True, hostname="host-94-26", container_id=info["Id"],
                  running=info["State"]["Running"], runtime=info["HostConfig"]["Runtime"],
                  network=info["HostConfig"]["NetworkMode"], processes=processes[:20],
                  sst_active=any(p[1] in ("sst", "sstsim.x") and not p[2].startswith("Z") for p in processes),
                  disk_free_bytes=shutil.disk_usage(WORK).free,
                  host_debuggers={name: shutil.which(name) for name in ("gdb", "lldb", "addr2line", "coredumpctl")},
                  core_pattern=Path("/proc/sys/kernel/core_pattern").read_text().strip())
    for filename in ("revisions.json", "progress.json", "build.json"):
        path = WORK / filename
        if path.exists():
            result[filename] = json.loads(path.read_text())
    obj = WORK / "tilexr_moonep_combine_ht_kernel.o"
    result["kernel_sha256"] = hashlib.sha256(obj.read_bytes()).hexdigest()
    parent = WORK / "cases/8p-bs4-a1"
    result["case"] = json.loads((parent / "case.json").read_text())
    result["phases"] = []
    for phase in ("send-only", "send-fault-a1", "send-fault-a2"):
        folder = parent / phase
        row = dict(phase=phase)
        for filename in ("run.json", "diagnosis.json"):
            path = folder / filename
            if path.exists():
                row[filename] = json.loads(path.read_text())
        log = folder / "sim.log"
        if log.exists():
            row["log_bytes"] = log.stat().st_size
            categories = {}
            matched = []
            with log.open(errors="replace") as handle:
                for line in handle:
                    for category in ("FATAL", "ERROR", "Segmentation fault", "SIGSEGV", "assert", "Simulation is complete"):
                        if category in line:
                            categories[category] = categories.get(category, 0) + 1
                            if len(matched) < 8:
                                matched.append(line.strip()[:700])
            row["error_counts"] = categories
            row["first_errors"] = matched
        row["core_files"] = [dict(name=p.name, bytes=p.stat().st_size) for p in sorted(folder.glob("core*"))[:4]]
        row["dump_files"] = len(list((folder / "bin").glob("*.bin")))
        result["phases"].append(row)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
