# Copyright (c) 2026 TileXR Project.
"""Read one retained SST candidate, its container state and scalar call metadata.

No container or process is started or stopped. The container's Python process
only reads configuration and optionally invokes its existing disassembler.
"""
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import sys


CONTAINER_PROBE = r'''
import json, pathlib, re, subprocess, sys
import yaml
folder = pathlib.Path(sys.argv[1])
processes = []
for entry in pathlib.Path("/proc").iterdir():
    if entry.name.isdigit():
        try:
            processes.append((entry / "comm").read_text().strip())
        except (FileNotFoundError, ProcessLookupError):
            pass
config = yaml.safe_load((folder / "capture/workload.yml").read_text())
chip = config["workload_config"]["chip0"]
out = {"address_config": {key: chip[key] for key in
       ("ub_base_addr", "stack_base_addr", "block_dim", "subblock_dim")}}
out["simulator_process_count"] = sum(bool(re.search(r"sst(sim)?|mpirun", name, re.I)) for name in processes)
out["compiler_process_count"] = sum(bool(re.search(r"bisheng|clang|ccec", name, re.I)) for name in processes)
root = pathlib.Path("/opt/sim/Ascend/cann-9.1.0")
tools = sorted({p for pattern in ("*/bin/*objdump*", "*/*/bin/*objdump*", "*/*/*/bin/*objdump*")
                for p in root.glob(pattern) if p.is_file()})
out["disassemblers"] = [str(p.relative_to(root)) for p in tools[:8]]
out["disassembly_attempted"] = False
if tools:
    tool = next((p for p in tools if p.name == "llvm-objdump"), tools[0])
    process = subprocess.run([str(tool), "-d", str(folder / "send.o")],
                             capture_output=True, text=True, timeout=25)
    out["disassembly_attempted"] = True
    out["disassembly_returncode"] = process.returncode
    instructions = [line.strip() for line in process.stdout.splitlines()
                    if re.match(r"^\s*[0-9a-f]+:\s", line)]
    out["instruction_count"] = len(instructions)
    stack = [line for line in instructions if re.search(r"\bsp\b", line, re.I)]
    out["stack_instruction_count"] = len(stack)
    out["stack_instructions"] = [line[:180] for line in stack[:6]]
    out["call_instruction_count"] = sum(bool(re.search(r"\b(call|bl|jal)\b", line, re.I))
                                        for line in instructions)
print(json.dumps(out, separators=(",", ":")))
'''


def call(argv, timeout=20):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, "query_failed_" + Path(argv[0]).name
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
        out["container_probe"] = json.loads(call(["docker", "exec", name, "python3", "-c",
                                                   CONTAINER_PROBE, str(Path("/work") / folder.name)], timeout=40))
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
