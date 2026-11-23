# Copyright (c) 2026 TileXR Project.
"""Inspect the retained compiler input and calls without launching a simulator.

The host verifies this session's isolated container. Its container process writes
preprocessed input, assembly and bounded compiler reports in a new subdirectory.
"""
import hashlib
from collections import Counter
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


def focus_calls(folder, report):
    text = (folder / "object-evidence-1/disassembly-0.log").read_text(errors="replace").split("\nSTDERR\n", 1)[0]
    functions = {}
    current = "<unlabelled>"
    for line in text.splitlines():
        label = re.match(r"^\s*([0-9a-f]+)\s+<([^>]+)>:\s*$", line, re.I)
        if label:
            current = label[2]
            continue
        instruction = re.match(r"^\s*([0-9a-f]+):\s+(.+)", line, re.I)
        if not instruction:
            continue
        tokens = instruction[2].split()
        while tokens and re.fullmatch(r"(?:[0-9a-f]{2}|[0-9a-f]{4}|[0-9a-f]{8}|[0-9a-f]{16})", tokens[0], re.I):
            tokens.pop(0)
        if tokens:
            functions.setdefault(current, []).append((line.strip(), tokens[0].lower()))
    rows, samples = [], []
    for name, instructions in functions.items():
        counts = Counter(mnemonic for _, mnemonic in instructions)
        control = {key: value for key, value in counts.items() if re.search(r"call|branch|ret|jmp|jump|bal|jal|^bl$|^br$", key)}
        if "BuildPutWqes" in name or "tilexr_moonep_combine_ht_kernel" in name or name == "<unlabelled>":
            rows.append({"name": name[:180], "instruction_count": len(instructions),
                         "common_opcodes": dict(counts.most_common(6)), "control_opcodes": control})
        if "BuildPutWqes" in name:
            samples.extend(line[:240] for line, _ in instructions[:2])
    kernel = next((instructions for name, instructions in functions.items()
                   if "tilexr_moonep_combine_ht_kernel" in name), [])
    samples.extend(line[:240] for line, _ in kernel[:4])
    if not samples:
        samples = [line[:240] for instructions in functions.values() for line, _ in instructions][:8]
    out = {"ok": True, "report": str(report), "object_sha256": EXPECTED_OBJECT,
           "simulator_launched": False, "function_count": len(functions), "functions": rows[:6],
           "instruction_samples": samples[:8]}
    return out


def inspect_object(folder, report, env, sst):
    out = {"ok": True, "report": str(report), "object_sha256": EXPECTED_OBJECT,
           "simulator_launched": False, "disassembly_available": False}
    preprocessed = folder / "compiler-evidence-1/input.ii"
    text = preprocessed.read_text(errors="replace")
    out["preprocessed_has_always_inline_builder"] = bool(re.search(
        r"always_inline[\s\S]{0,200}?void\s+BuildPutWqes\s*\(", text))
    out["preprocessed_mentions_local_builder"] = str(folder / "simd_wqe_interleave.h") in text
    reloc = subprocess.run(["readelf", "-rW", str(folder / "send_rel.o")],
                           check=True, capture_output=True, text=True, timeout=15).stdout
    (report / "relocations.txt").write_text(reloc)
    refs = [line.strip() for line in reloc.splitlines() if "BuildPutWqes" in line]
    out["builder_relocation_count"] = len(refs)
    out["builder_relocation_samples"] = [line[:240] for line in refs[:2]]
    root = Path(env["cann_root"])
    compiler = Path(env["compiler"]).resolve()
    tools = {p for pattern in ("*/bin/*objdump*", "*/*/bin/*objdump*", "*/*/*/bin/*objdump*")
             for p in root.glob(pattern) if p.is_file()}
    tools.update(p for p in compiler.parent.glob("*objdump*") if p.is_file())
    out["disassemblers"] = [str(p) for p in sorted(tools)[:6]]
    out["disassembler_queries"] = []
    for index, tool in enumerate(sorted(tools)[:4]):
        result = subprocess.run(sst.shell_command(env, "cann", [tool, "-d", folder / "send.o"]),
                                capture_output=True, timeout=25)
        (report / ("disassembly-" + str(index) + ".log")).write_bytes(result.stdout + b"\nSTDERR\n" + result.stderr)
        lines = result.stdout.decode(errors="replace").splitlines()
        instructions = [line.strip() for line in lines if re.match(r"^\s*[0-9a-f]+:\s", line, re.I)]
        out["disassembler_queries"].append({"tool": tool.name, "returncode": result.returncode,
                                           "instruction_count": len(instructions)})
        if result.returncode == 0 and instructions:
            out["disassembly_available"] = True
            out["instruction_count"] = len(instructions)
            named = [line for line in instructions if "BuildPutWqes" in line]
            stack = [line for line in instructions if re.search(r"\bsp\b", line, re.I)]
            out["builder_instruction_reference_count"] = len(named)
            out["builder_instruction_samples"] = [line[:240] for line in named[:4]]
            out["stack_instruction_count"] = len(stack)
            out["stack_instruction_samples"] = [line[:200] for line in stack[:4]]
            break
    if not out["disassembly_available"]:
        result = subprocess.run(sst.shell_command(env, "cann", [env["compiler"], "--help"]),
                                capture_output=True, timeout=20)
        (report / "compiler-help.log").write_bytes(result.stdout + result.stderr)
        lines = (result.stdout + result.stderr).decode(errors="replace").splitlines()
        out["compiler_help_returncode"] = result.returncode
        out["compiler_output_options"] = [line.strip()[:180] for line in lines
            if re.search(r"save-temps|emit.*llvm|assembly|dump|device-only|cce.*only", line, re.I)][:10]
    return out


def inspect_container(args):
    sys.path.insert(0, "/baseline")
    import sst
    work = Path("/work")
    record = json.loads((work / "trial-result.json").read_text())
    folder = Path(record["report"])
    folder.relative_to(work)
    assert folder.name == "mte-interleave-1"
    assert hashlib.sha256((folder / "send.o").read_bytes()).hexdigest() == EXPECTED_OBJECT
    prefix = {"object-inspect": "object-evidence-", "call-focus": "call-evidence-"}.get(args["action"], "compiler-evidence-")
    report = folder / (prefix + str(args["inspection_attempt"]))
    report.mkdir()
    out = {"ok": False, "report": str(report), "object_sha256": EXPECTED_OBJECT,
           "stages": [], "simulator_launched": False}
    try:
        if args["action"] == "call-focus":
            return save_result(work, report, focus_calls(folder, report))
        snapshot = json.loads(Path("/baseline/environment.snapshot.json").read_text())
        env = snapshot["settings"]
        if args["action"] == "object-inspect":
            out = inspect_object(folder, report, env, sst)
            return save_result(work, report, out)
        source = folder / "combine_ht.cpp"
        builder = folder / "simd_wqe_interleave.h"
        text = builder.read_text()
        out["builder_source_sha256"] = hashlib.sha256(builder.read_bytes()).hexdigest()
        out["source_has_always_inline"] = "__attribute__((always_inline)) inline __aicore__ void BuildPutWqes(" in text
        out["header_includes_local_builder"] = '#include "simd_wqe_interleave.h"' in (folder / "combine_ht_kernel.h").read_text()
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
    return save_result(work, report, out)


def save_result(work, report, out):
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
