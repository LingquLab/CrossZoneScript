"""Inspect bounded post-frame-lowering machine dumps and verify VF code bytes."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import resource
import struct
import subprocess
import sys
from vf_inspect import CASES, host, object_function

SUBJECTS = (CASES[3],
            ("mte-scalar_store_post_runtime_gather-24", "b9cfe94b49ffef820bb5380a189d88a44e2def774730ad0287b439c2199e1468"),
            ("mte-interleave_predicate_runtime-28", "a7e528db61b9cd9f132fdbeaa4e9464e3865aa7e8f596e2c5a1dcfb4ac9459ab"))


def function_code(path, name):
    blob = path.read_bytes()
    assert blob[:6] == b"\x7fELF\x02\x01" and len(blob) < 64 * 1024 * 1024
    sections = struct.unpack_from("<Q", blob, 40)[0]
    entry_size, count = struct.unpack_from("<HH", blob, 58)
    assert entry_size >= 64
    symbols = subprocess.run(["readelf", "-sW", str(path)], check=True,
                             capture_output=True, text=True, timeout=10).stdout
    for line in symbols.splitlines():
        fields = line.split()
        if len(fields) >= 8 and fields[3] == "FUNC" and fields[7] == name and fields[6].isdigit():
            assert int(fields[6]) < count
            section = struct.unpack_from("<IIQQQQIIQQ", blob, sections + int(fields[6]) * entry_size)
            address, size = int(fields[1], 16), int(fields[2], 0)
            begin = section[4] + address - section[3]
            assert size > 0 and address >= section[3] and begin + size <= section[4] + section[5]
            return {"size": size, "sha256": hashlib.sha256(blob[begin:begin + size]).hexdigest()}
    return None


def machine_function(text, aliases):
    headers = list(re.finditer(r"(?m)^# Machine code for function ([^\n:]+):[^\n]*\n", text))
    for alias in aliases:
        for header in headers:
            if header[1].strip() != alias:
                continue
            end = text.find("# End machine code for function", header.end())
            body = text[header.end():end if end >= 0 else len(text)].splitlines()
            instructions = []
            for line in body:
                code = line.strip()
                if not code or code.startswith("#") or code.endswith(":"):
                    continue
                match = re.fullmatch(r"(?:[^=\n]+\s*=\s*)?([A-Z][A-Z0-9_]+)(?:\s+(.+))?", code)
                if match and not match[1].startswith("DBG_"):
                    instructions.append((match[1], code[:450]))
            return alias, body, instructions
    return None, [], []


def log_limit():
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))


def inside_container():
    sys.path.insert(0, "/baseline")
    import sst
    root, report = Path("/work"), Path("/work/vf-machine-inspection-1")
    report.mkdir()
    snapshot = json.loads(Path("/baseline/environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    env = snapshot["settings"]
    out = {"diagnostic_only": True, "simulation_started": False, "source_modified": False,
           "machine_dump_available": False, "cases": [], "source_snippets": []}
    for index, (name, digest) in enumerate(SUBJECTS):
        binding = object_function(root, name, digest)
        folder = root / name
        inputs = [path for path in folder.iterdir() if path.suffix in (".cpp", ".h")]
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
        command = json.loads((folder / "build.json").read_text())[0]
        assert command[0] == env["compiler"] and command.count("-c") == command.count("-o") == 1
        assert Path(command[command.index("-c") + 1]) == folder / "combine_ht.cpp"
        original = folder / "send_rel.o"
        output, log = report / (str(index) + ".o"), report / (str(index) + ".log")
        symbol = binding["name"]
        original_digest = hashlib.sha256(original.read_bytes()).hexdigest()
        old_code = function_code(original, symbol)
        base = symbol[:-len(".vector.thread")] if symbol.endswith(".vector.thread") else symbol
        aliases = (symbol, base + ".extracted", base)
        command[command.index("-o") + 1] = str(output)
        command.extend(["-mllvm", "-print-after=prologepilog", "-mllvm", "-filter-print-funcs=" + ",".join(aliases)])
        with log.open("wb") as stream:
            try:
                process = subprocess.run(sst.shell_command(env, "cann", command), cwd=report,
                    stdout=stream, stderr=subprocess.STDOUT, timeout=65, preexec_fn=log_limit)
                code = process.returncode
            except subprocess.TimeoutExpired:
                code = 124
        row = {"case": name, "validated_object_sha256": digest, "compiler_returncode": code,
               "machine_instruction_rows": 0, "dump_bytes": log.stat().st_size}
        text = log.read_text(errors="replace")
        selected, body, instructions = machine_function(text, aliases)
        row["function"] = selected
        row["machine_instruction_rows"] = len(instructions)
        row["opcode_counts"] = dict(Counter(opcode for opcode, _ in instructions).most_common(10))
        row["printed_function_names"] = re.findall(r"(?m)^# Machine code for function ([^\n:]+):", text)[:3]
        frames = [line.strip()[:450] for line in body if re.search(r"\bsp\b|\bfp\b|stack|spill|\bfi#", line, re.I)]
        row["frame_related_line_count"] = len(frames)
        row["stack_offsets"] = sorted(set(int(value) for value in re.findall(r"\bSP([+-][0-9]+)\b", "\n".join(frames))))[:12]
        new_code = function_code(output, symbol) if output.is_file() and code == 0 else None
        row["retained_vf_code"] = old_code
        row["instrumented_vf_code"] = new_code
        row["vf_code_identical"] = bool(old_code and new_code and old_code == new_code)
        row["source_unchanged"] = before == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
        row["retained_object_unchanged"] = hashlib.sha256((folder / "send.o").read_bytes()).hexdigest() == digest
        row["retained_relocatable_unchanged"] = hashlib.sha256(original.read_bytes()).hexdigest() == original_digest
        assert row["source_unchanged"] and row["retained_object_unchanged"]
        assert row["retained_relocatable_unchanged"]
        if index == 1:
            samples = [line for opcode, line in instructions if "ADD" in opcode and re.search(r"\b(?:14|15)\b", line)]
            samples += [line for opcode, line in instructions if "ST" in opcode]
        else:
            samples = frames + [line for opcode, line in instructions if "ST" in opcode]
        out["source_snippets"].extend({"case": name, "machine": line} for line in samples[:(3, 3, 2)[index]])
        if code:
            row["compiler_diagnostic"] = [line[:220] for line in text.splitlines()
                                          if "error:" in line or "warning:" in line][:2]
        out["cases"].append(row)
        (root / "vf-machine-result.json").write_text(json.dumps(out, indent=2))
        if code or not instructions or not row["vf_code_identical"]:
            break
    out["machine_dump_available"] = len(out["cases"]) == len(SUBJECTS) and all(
        row["compiler_returncode"] == 0 and row["machine_instruction_rows"] and row["vf_code_identical"]
        for row in out["cases"])
    (root / "vf-machine-result.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, separators=(",", ":")))


if __name__ == "__main__":
    try:
        if sys.argv[1] == "--container":
            inside_container()
        else:
            host(json.loads(sys.argv[1]), entry="vf_machine.py", directory="vf-machine-inspection-1", timeout=330)
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error)[:100]}))
        sys.exit(1)
