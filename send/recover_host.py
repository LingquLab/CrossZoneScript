# Copyright (c) 2026 TileXR Project.
"""Read one retained SST candidate, its container state and scalar call metadata.

No container or process is started or stopped. Process names are checked from
the host through the container's PID namespace; generated YAML stays on disk.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys


def process_counts(init_pid):
    namespace = os.readlink(Path("/proc") / str(init_pid) / "ns/pid")
    names = []
    inaccessible = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if os.readlink(entry / "ns/pid") == namespace:
                names.append((entry / "comm").read_text().strip())
        except (FileNotFoundError, ProcessLookupError):
            pass
        except PermissionError:
            inaccessible += 1
    return {"observed_process_count": len(names),
            "inaccessible_pid_count": inaccessible,
            "simulator_process_count": sum(bool(re.search(r"sst(sim)?|mpirun", name, re.I)) for name in names),
            "compiler_process_count": sum(bool(re.search(r"bisheng|clang|ccec", name, re.I)) for name in names)}


def call(argv, timeout=20):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        sys.stderr.write(result.stderr[-4000:])
        raise RuntimeError("query_failed_" + Path(argv[0]).name + "_" + str(result.returncode))
    return result.stdout


def main(args):
    assert socket.gethostname().split(".")[0] == "host-94-26"
    session = args["previous_session"]
    assert session == "cb41ec7d-5530-4f29-981d-6274e209a40e"
    work = Path("/var/tmp") / ("tilexr-combine-ht-" + session + "-v11")
    record = json.loads((work / "trial-result.json").read_text())
    folder = work / Path(record["report"]).relative_to("/work")
    folder.resolve().relative_to(work.resolve())
    assert folder.name == "mte-interleave-5"
    digest = hashlib.sha256((folder / "send.o").read_bytes()).hexdigest()
    assert digest == "1c52ca2de71e4a0179585c6c027a5ee08c2ef888f9d5a99203395b15a5e1e5e7"
    name = "tilexr-combine-ht-" + session[:8] + "-v11"
    info = json.loads(call(["docker", "inspect", "--format", "{{json .}}", name]))
    assert info["Config"]["Labels"].get("crosszone.session") == session
    assert info["HostConfig"]["NetworkMode"] == "none"
    assert info["Image"] == "sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f"
    out = dict(identity_ok=True, container_running=info["State"]["Running"],
               container_id=info["Id"][:12], image_id=info["Image"],
               report=folder.name, object_sha256=digest,
               object_matches_previous_result=digest == record["object_sha256"],
               previous_simulator_returncode=record.get("returncode"),
               previous_wqe_verified=record.get("wqe_verified"))
    symbols = call(["readelf", "-sW", str(folder / "send.o")])
    functions = [line.split() for line in symbols.splitlines() if " FUNC " in line]
    out["function_count"] = len(functions)
    out["builder_functions"] = [{"symbol": words[-1][:220], "size": int(words[2])}
                                for words in functions if "BuildPutWqes" in words[-1]][:4]
    if out["container_running"]:
        out.update(process_counts(info["State"]["Pid"]))
    config = folder / "capture/workload.yml"
    out["workload_exists"] = config.is_file()
    if config.is_file():
        text = config.read_text()
        out["address_config"] = {key: sorted({int(value, 0) for value in re.findall(
            r"(?m)^\s*" + key + r":\s*(0x[0-9a-fA-F]+|[0-9]+)\s*$", text)})
            for key in ("ub_base_addr", "stack_base_addr", "block_dim", "subblock_dim")}
    log = folder / "capture/sim.log"
    with log.open("rb") as stream:
        stream.seek(max(0, log.stat().st_size - 32768))
        lines = stream.read().decode(errors="replace").splitlines()
    fatal = next((line for line in lines if "FATAL:" in line), "")
    address = re.search(r"\[addr=\s*(\d+)\]", fatal)
    cycle = re.search(r"\[Sim:\s*(\d+)\(cycle\)\]", fatal)
    out["fatal"] = dict(kind="unknown_scalar_memory" if "LsuL1: Unknown low level mem" in fatal else "other",
                        address=int(address[1]) if address else None,
                        cycle=int(cycle[1]) if cycle else None)
    print(json.dumps(out, separators=(",", ":")))


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
