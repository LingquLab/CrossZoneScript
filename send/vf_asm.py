"""Inspect compiler-generated VF assembly without running an SST workload."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from vf_inspect import CASES, host, object_function

SUBJECTS = (("mte-scalar_store_post_runtime_gather-24", "b9cfe94b49ffef820bb5380a189d88a44e2def774730ad0287b439c2199e1468"),
            ("mte-interleave_predicate_runtime-28", "a7e528db61b9cd9f132fdbeaa4e9464e3865aa7e8f596e2c5a1dcfb4ac9459ab"),
            CASES[2])


def assembly_function(text, name):
    lines = text.splitlines()
    begin = next((index for index, line in enumerate(lines)
                  if re.match(r'^\s*"?' + re.escape(name) + r'"?:\s*(?:(?:#|//).*)?$', line)), None)
    if begin is None:
        return [], []
    end = next((index for index in range(begin + 1, len(lines))
                if re.match(r"\s*\.size\s+\"?" + re.escape(name), lines[index])
                or re.match(r"\s*\.Lfunc_end\d+:|\s*\.cfi_endproc", lines[index])), len(lines))
    body = lines[begin + 1:end]
    instructions = []
    for line in body:
        code = re.split(r"//|#", line, maxsplit=1)[0].strip()
        if not code or code.startswith(".") or code.endswith(":"):
            continue
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_.]*)(?:\s+(.+))?", code)
        if match:
            instructions.append((match[1].lower(), code[:450]))
    return body, instructions


def inside_container():
    sys.path.insert(0, "/baseline")
    import sst
    root, report = Path("/work"), Path("/work/vf-asm-inspection-1")
    report.mkdir()
    snapshot = json.loads(Path("/baseline/environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    env = snapshot["settings"]
    out = {"diagnostic_only": True, "simulation_started": False, "source_modified": False,
           "assembly_available": False, "cases": [], "source_snippets": []}

    def run(label, argv, timeout):
        log = report / (label + ".log")
        with log.open("wb") as stream:
            try:
                process = subprocess.run(sst.shell_command(env, "cann", argv), cwd=report,
                                         stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
                code = process.returncode
            except subprocess.TimeoutExpired:
                code = 124
        return code, log

    for index, (name, digest) in enumerate(SUBJECTS):
        binding = object_function(root, name, digest)
        folder = root / name
        inputs = [path for path in folder.iterdir() if path.suffix in (".h", ".cpp")]
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
        command = json.loads((folder / "build.json").read_text())[0]
        assert command[0] == env["compiler"] and command.count("-c") == command.count("-o") == 1
        assert Path(command[command.index("-c") + 1]) == folder / "combine_ht.cpp"
        output = report / ("case-" + str(index) + ".s")
        command[command.index("-c")] = "-S"
        command[command.index("-o") + 1] = str(output)
        code, log = run("assembly-" + str(index), command, 65)
        row = {"case": name, "validated_object_sha256": digest, "compiler_returncode": code,
               "output_exists": output.is_file(), "assembly_instruction_rows": 0}
        if index == 1:
            dry = json.loads((folder / "build.json").read_text())[0]
            dry[dry.index("-o") + 1] = str(report / "dry-run.o")
            dry.append("-###")
            row["driver_query_returncode"], driver_log = run("driver-query", dry, 20)
            tokens = []
            for line in driver_log.read_text(errors="replace").splitlines():
                try:
                    tokens.extend(shlex.split(line))
                except ValueError:
                    continue
            row["vf_stack_options"] = [token[:160] for token in tokens
                                       if "vf" in token.lower() and "stack" in token.lower()][:8]
        if output.is_file() and 0 < output.stat().st_size < 32 * 1024 * 1024:
            blob = output.read_bytes()
            row.update(output_bytes=len(blob), output_sha256=hashlib.sha256(blob).hexdigest())
            row["output_format"] = "ELF" if blob.startswith(b"\x7fELF") else "LLVM_bitcode" if blob.startswith(b"BC\xc0\xde") else "text"
            if row["output_format"] == "text":
                text = blob.decode(errors="replace")
                body, instructions = assembly_function(text, binding["name"])
                row.update(function=binding["name"], function_label_found=bool(body),
                           assembly_instruction_rows=len(instructions),
                           opcode_counts=dict(Counter(opcode for opcode, _ in instructions).most_common(10)))
                stacks = [line.strip()[:450] for line in body if re.search(r"\bsp\b|\bfp\b|stack|spill", line, re.I)]
                row["stack_related_line_count"] = len(stacks)
                if index == 0:
                    values = [line for opcode, line in instructions if "add" in opcode and re.search(r"\b(?:14|15)\b", line)]
                    values += [line for opcode, line in instructions if "st" in opcode]
                elif index == 1:
                    values = stacks or [line for _, line in instructions]
                else:
                    values = [line for opcode, line in instructions if "st" in opcode]
                if not values:
                    values = [line.strip()[:450] for line in text.splitlines() if binding["name"] in line][:2]
                out["source_snippets"].extend({"case": name, "assembly": line}
                    for line in values[:(3, 3, 2)[index]])
        row["source_unchanged"] = before == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
        row["retained_object_unchanged"] = hashlib.sha256((folder / "send.o").read_bytes()).hexdigest() == digest
        assert row["source_unchanged"]
        assert row["retained_object_unchanged"]
        if code:
            row["compiler_diagnostic"] = [line[:220] for line in log.read_text(errors="replace").splitlines()
                                          if "error:" in line or "warning:" in line][:2]
        out["cases"].append(row)
        (root / "vf-asm-result.json").write_text(json.dumps(out, indent=2))
        if code or not row["assembly_instruction_rows"]:
            break
    out["assembly_available"] = len(out["cases"]) == len(SUBJECTS) and all(row["assembly_instruction_rows"] for row in out["cases"])
    (root / "vf-asm-result.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, separators=(",", ":")))


if __name__ == "__main__":
    try:
        if sys.argv[1] == "--container":
            inside_container()
        else:
            host(json.loads(sys.argv[1]), entry="vf_asm.py", directory="vf-asm-inspection-1", timeout=260)
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error)[:100]}))
        sys.exit(1)
