# Copyright (c) 2026 TileXR Project.
"""Inspect the retained compiler input and calls without launching a simulator.

The host verifies this session's isolated container. Its container process writes
preprocessed input, assembly and bounded compiler reports in a new subdirectory.
"""
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import sys

SESSION = "05d1d635-d6d7-4dd3-af34-421e32bb209c"
EXPECTED_OBJECT = "1c52ca2de71e4a0179585c6c027a5ee08c2ef888f9d5a99203395b15a5e1e5e7"


def command_for(command, mode, destination):
    command = list(command)
    assert command.count("-c") == 1 and command.count("-o") == 1
    command[command.index("-c")] = mode
    command[command.index("-o") + 1] = str(destination)
    return command


def inspect_container(args):
    sys.path.insert(0, "/baseline")
    import sst
    work = Path("/work")
    record = json.loads((work / "trial-result.json").read_text())
    folder = Path(record["report"])
    folder.relative_to(work)
    assert folder.name == "mte-interleave-1"
    assert hashlib.sha256((folder / "send.o").read_bytes()).hexdigest() == EXPECTED_OBJECT
    report = folder / ("compiler-evidence-" + str(args["inspection_attempt"]))
    report.mkdir()
    out = {"ok": False, "report": str(report), "object_sha256": EXPECTED_OBJECT,
           "stages": [], "simulator_launched": False}
    try:
        source = folder / "combine_ht.cpp"
        builder = folder / "simd_wqe_interleave.h"
        text = builder.read_text()
        out["builder_source_sha256"] = hashlib.sha256(builder.read_bytes()).hexdigest()
        out["source_has_always_inline"] = "__attribute__((always_inline)) inline __aicore__ void BuildPutWqes(" in text
        out["header_includes_local_builder"] = '#include "simd_wqe_interleave.h"' in (folder / "combine_ht_kernel.h").read_text()
        snapshot = json.loads(Path("/baseline/environment.snapshot.json").read_text())
        env = snapshot["settings"]
        command = json.loads((folder / "build.json").read_text())[0]
        assert command[0] == env["compiler"]
        assert command[command.index("-c") + 1] == str(source)
        out["compile_source_matches_report"] = True
        for name, mode, destination in (("preprocess", "-E", report / "input.ii"),
                                        ("assembly", "-S", report / "kernel.s")):
            argv = command_for(command, mode, destination)
            with (report / (name + ".log")).open("wb") as log:
                result = subprocess.run(sst.shell_command(env, "cann", argv), stdout=log,
                                        stderr=subprocess.STDOUT, timeout=75)
            stage = {"name": name, "returncode": result.returncode, "output_exists": destination.is_file()}
            out["stages"].append(stage)
            if result.returncode:
                lines = (report / (name + ".log")).read_text(errors="replace").splitlines()
                out["compile_errors"] = [line[:250] for line in lines if "error:" in line][:2]
                raise RuntimeError(name + "_failed")
            assert destination.is_file(), name + "_output_missing"
        preprocessed = (report / "input.ii").read_text(errors="replace")
        out["preprocessed_has_always_inline_builder"] = bool(re.search(
            r"always_inline[\s\S]{0,200}?void\s+BuildPutWqes\s*\(", preprocessed))
        assembly = (report / "kernel.s").read_text(errors="replace")
        references = [line.strip() for line in assembly.splitlines() if "BuildPutWqes" in line]
        calls = [line for line in references if re.search(r"\b(call|bl|jal)\b", line, re.I)]
        out["builder_reference_count"] = len(references)
        out["builder_named_call_count"] = len(calls)
        out["builder_reference_samples"] = [("call " + line if line in calls else line)[:240]
                                             for line in (calls + [line for line in references if line not in calls])[:6]]
        out["stack_reference_count"] = sum(bool(re.search(r"\bsp\b", line, re.I)) for line in assembly.splitlines())
        out["ok"] = True
    except Exception as error:
        out.update(error=type(error).__name__, failure=str(error)[:160])
    (report / "result.json").write_text(json.dumps(out, indent=2))
    (work / "compiler-result.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, separators=(",", ":")))
    return 0 if out["ok"] else 1


def host(args):
    assert socket.gethostname().split(".")[0] == args["hostname"] == "host-94-26"
    work = Path(__file__).resolve().parent
    assert work.name == "tilexr-combine-ht-" + SESSION + "-v11"
    if args["action"] == "compiler-result":
        print(json.dumps(json.loads((work / "compiler-result.json").read_text()), separators=(",", ":")))
        return 0
    assert type(args["inspection_attempt"]) is int and 1 <= args["inspection_attempt"] <= 20
    name = "tilexr-combine-ht-" + SESSION[:8] + "-v11"
    check = subprocess.run(["docker", "inspect", "--format", "{{json .}}", name],
                           check=True, capture_output=True, text=True, timeout=20)
    info = json.loads(check.stdout)
    assert info["Config"]["Labels"].get("crosszone.session") == SESSION
    assert info["State"]["Running"] and info["HostConfig"]["NetworkMode"] == "none"
    assert info["Image"] == "sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f"
    assert any(m["Source"] == str(work) and m["Destination"] == "/work" and m["RW"] for m in info["Mounts"])
    result = subprocess.run(["docker", "exec", name, "python3", "/work/compiler_inspect.py",
                             json.dumps(args), "--container"], capture_output=True, timeout=180)
    (work / "compiler-host.log").write_bytes(result.stdout + b"\nSTDERR\n" + result.stderr)
    assert result.returncode == 0, "compiler_inspection_failed"
    value = json.loads(result.stdout)
    assert value["ok"]
    print(json.dumps(value, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    arguments = json.loads(sys.argv[1])
    sys.exit(inspect_container(arguments) if sys.argv[-1] == "--container" else host(arguments))
