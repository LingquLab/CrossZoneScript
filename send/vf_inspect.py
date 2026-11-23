"""Inspect retained VF objects and installed compiler tools without compilation."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import sys

SESSION = "7626e689-38e9-468b-9a82-63673422e9d3"
NAME = "tilexr-combine-ht-" + SESSION[:8] + "-v11"
IMAGE = "sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f"
CASES = (("mte-scalar_store_post_runtime_vector-23", "8affc10bb6a98ee7b3ff36bb6f924d2cbb505c49179bb7dca6a2e6ac15f6d32a"),
         ("mte-interleave-22", "eaa4a8f76eab02b9ee556caec8aa2f9f903ed67c5bde7157318238ec6d47f23c"),
         ("mte-scalar_store_post-11", "2e9b4ebf6ce8eea49bd3d6364da2a08675df028eef91a8455d918c549176c308"),
         ("mte-scalar_pointer_runtime64-17", "db4e0e450e240d903c4373d66a0537df217326751d6ba474cfb3eaa616be8702"))


def instructions(text, begin, size):
    rows = []
    for line in text.splitlines():
        match = re.match(r"^\s*([0-9a-fA-F]+):\s+(.+)", line)
        if not match or not begin <= int(match[1], 16) < begin + size:
            continue
        tokens = match[2].split()
        while tokens and re.fullmatch(r"(?:[0-9a-fA-F]{2}|[0-9a-fA-F]{8}|[0-9a-fA-F]{16})", tokens[0]):
            tokens.pop(0)
        if tokens and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]*", tokens[0]):
            rows.append((tokens[0].lower(), " ".join(tokens)[:250]))
    return rows


def object_function(root, name, expected_sha):
    folder = root / name
    record = json.loads((folder / "trial-result.json").read_text())
    binary = folder / "send.o"
    assert 0 < binary.stat().st_size <= 10485760
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == record["object_sha256"] == expected_sha
    symbols = subprocess.run(["readelf", "-sW", str(binary)], check=True,
                             capture_output=True, text=True, timeout=10).stdout
    candidates, all_functions = [], []
    for line in symbols.splitlines():
        fields = line.split()
        if len(fields) >= 8 and fields[3] == "FUNC":
            row = {"name": fields[7], "address": int(fields[1], 16), "size": int(fields[2], 0)}
            all_functions.append(row)
        else:
            continue
        if fields[7].endswith(".vector.thread"):
            if "ProbeOperation" in fields[7] or "BuildPutWqes" in fields[7]:
                candidates.append(row)
    assert candidates, "target_vector_function_missing"
    selected = min(candidates, key=lambda row: ("BuildPutWqesILb0" not in row["name"], row["size"]))
    fault = {}
    log = folder / "capture/sim.log"
    if record.get("returncode") and log.is_file():
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size - 32768))
            tail = stream.read().decode(errors="replace")
        start = tail.find("FATAL:")
        window = tail[start:].split("\n", 1)[0] if start >= 0 else ""
        for key in ("pc", "link", "lr", "addr"):
            match = re.search(r"\b" + key + r"\s*[:=]\s*(0x[0-9a-fA-F]+|[0-9]+)", window, re.I)
            if match:
                fault[key] = int(match[1], 16 if match[1].lower().startswith("0x") else 10)
    matched = [row for row in all_functions if fault.get("pc") is not None
               and row["address"] <= fault["pc"] < row["address"] + row["size"]]
    if matched:
        selected = min(matched, key=lambda row: row["size"])
    begin = max(selected["address"], fault["pc"] - 16) if matched else selected["address"]
    end = min(selected["address"] + selected["size"], fault["pc"] + 16) if matched else begin + selected["size"]
    return {"case": name, "object_sha256": record["object_sha256"], "binary": binary,
            "fault_context": fault, "selected_by": "fault_pc" if matched else "vector_symbol",
            "decode_start": begin, "decode_size": end - begin, **selected}


def inside_container():
    sys.path.insert(0, "/baseline")
    import sst
    root = Path("/work")
    report = root / "vf-inspection-1"
    report.mkdir()
    snapshot = json.loads(Path("/baseline/environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    env = snapshot["settings"]
    def run(label, argv, timeout=15):
        command = sst.shell_command(env, "cann", argv)
        try:
            result = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as error:
            partial = error.stdout or ""
            if isinstance(partial, bytes):
                partial = partial.decode(errors="replace")
            result = subprocess.CompletedProcess(command, 124, partial, "bounded_tool_query_timed_out")
        (report / (label + ".log")).write_text(result.stdout + "\nSTDERR\n" + result.stderr)
        return result
    version = run("compiler-version", [env["compiler"], "--version"])
    help_text = run("compiler-help", [env["compiler"], "--help"], 20)
    out = {"inspection_complete": True, "simulation_started": False, "recompiled": False,
           "compiler_version": [line[:240] for line in version.stdout.splitlines()[:5]],
           "compiler_version_returncode": version.returncode, "compiler_help_returncode": help_text.returncode,
           "compiler_output_options": [line.strip()[:160] for line in help_text.stdout.splitlines()
                                       if re.search(r"save-temps|emit.*llvm|dump.*(?:ir|asm)|vf.*stack|stack.*vf|device-only", line, re.I)][:8],
           "disassembly_available": False, "decoder_tools": [], "decoder_queries": [], "functions": [], "instruction_samples": []}
    functions = [object_function(root, name, digest) for name, digest in CASES]
    compiler = Path(env["compiler"]).resolve()
    cann = Path(env["cann_root"])
    tools = set(compiler.parent.glob("*objdump*"))
    for pattern in ("*/bin/*objdump*", "*/*/bin/*objdump*", "*/*/*/bin/*objdump*"):
        tools.update(cann.glob(pattern))
    tools = sorted({path.resolve() for path in tools if path.is_file()})[:2]
    representative = functions[0]
    decoder, cached = None, None
    def decode(tool, cpu, function, label):
        result = run(label, [str(tool), "-d", "--mcpu=" + cpu,
                     "--start-address=" + hex(function["decode_start"]),
                     "--stop-address=" + hex(function["decode_start"] + function["decode_size"]),
                     "--disassemble-symbols=" + function["name"], str(function["binary"])])
        rows = instructions(result.stdout, function["decode_start"], function["decode_size"])
        out["decoder_queries"].append({"tool": tool.name, "cpu": cpu, "case": function["case"],
                                       "returncode": result.returncode, "decoded_instructions": len(rows),
                                       "diagnostic": [line[:180] for line in result.stderr.splitlines()[:1]]})
        return rows if result.returncode == 0 else []
    for index, tool in enumerate(tools):
        tool_version = run("decoder-version-" + str(index), [str(tool), "--version"])
        targets = tool_version.stdout.split("Registered Targets:", 1)
        out["decoder_tools"].append({"tool": str(tool), "version": tool_version.stdout.splitlines()[:1],
                                     "targets": [line.strip().split(" - ", 1)[0][:40]
                                                 for line in targets[-1].splitlines() if line.strip()][:20] if len(targets) == 2 else []})
        for cpu_index, cpu in enumerate(("dav-c310", "dav-c310-vec")):
            rows = decode(tool, cpu, representative, f"decoder-{index}-{cpu_index}")
            if rows:
                decoder, cached = (tool, cpu), rows
                break
        if decoder:
            break
    for index, function in enumerate(functions):
        row = {key: function[key] for key in ("case", "object_sha256", "size", "fault_context", "selected_by", "decode_start", "decode_size")}
        row["function"] = function["name"][:220]
        decoded = cached if index == 0 and decoder else (
            decode(*decoder, function, "case-" + str(index)) if decoder else [])
        row["opcode_counts"] = dict(Counter(opcode for opcode, _ in decoded))
        row["decoded_instructions"] = len(decoded)
        out["functions"].append(row)
        limit = min(2, 8 - len(out["instruction_samples"]))
        specific = [line for _, line in decoded if re.search(r"\b(?:v|r|x)(?:13|14|15)\b|\bsp\b", line, re.I)]
        stores = [line for _, line in decoded if re.search(r"vsts|vst|ld.*\[", line, re.I) and line not in specific]
        interesting = specific + stores
        out["instruction_samples"].extend({"case": function["case"], "instruction": line}
                                          for line in (interesting or [line for _, line in decoded])[:limit])
    out["disassembly_available"] = decoder is not None
    (root / "vf-inspection-result.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, separators=(",", ":")))


def host(args, entry="vf_inspect.py", directory="vf-inspection-1", timeout=300):
    assert (entry, directory) in (("vf_inspect.py", "vf-inspection-1"), ("vf_ir.py", "vf-ir-inspection-1"))
    assert socket.gethostname().split(".")[0] == args["hostname"] == "host-94-26"
    root = Path(__file__).resolve().parent
    assert root.name == "tilexr-combine-ht-" + SESSION + "-v11"
    assert not (root / directory).exists(), "inspection_already_attempted"
    process = subprocess.run(["docker", "inspect", NAME], capture_output=True, check=True, timeout=20)
    info = json.loads(process.stdout)[0]
    assert info["Image"] == IMAGE and info["Config"]["Labels"]["crosszone.session"] == SESSION
    assert info["State"]["Running"] and info["HostConfig"]["NetworkMode"] == "none"
    assert info["HostConfig"]["Runtime"] == "runc"
    assert any(m["Source"] == str(root) and m["Destination"] == "/work" and m["RW"] for m in info["Mounts"])
    assert any(m["Destination"] == "/baseline" and not m["RW"] for m in info["Mounts"])
    process = subprocess.run(["docker", "exec", NAME, "python3", "/work/" + entry, "--container"],
                             capture_output=True, timeout=timeout)
    (root / (directory + "-host.log")).write_bytes(process.stdout + b"\nSTDERR\n" + process.stderr)
    assert process.returncode == 0, "vf_inspection_failed"
    value = json.loads(process.stdout)
    packet = json.dumps(value, separators=(",", ":"))
    assert len(packet.encode()) < 12000
    print(packet)


if __name__ == "__main__":
    try:
        inside_container() if sys.argv[1] == "--container" else host(json.loads(sys.argv[1]))
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error)[:100]}))
        sys.exit(1)
