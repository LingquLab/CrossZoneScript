"""Inspect VF stack definitions and identical-source LLVM IR in private outputs."""
import hashlib
import json
from collections import Counter
from pathlib import Path
import re
import shutil
import subprocess
import sys
from vf_inspect import CASES, host, object_function


def parameter_count(parameters):
    if not parameters.strip() or parameters.strip() == "void":
        return 0
    depth, count, quoted = 0, 1, False
    for index, char in enumerate(parameters):
        if char == '"' and (not index or parameters[index - 1] != "\\"):
            quoted = not quoted
        elif not quoted:
            depth += (char in "([{<") - (char in ")]}>")
            count += char == "," and depth == 0
    return count


def summarize_ir(text):
    records = []
    for match in re.finditer(r"(?ms)^define\b(.*?)^}", text):
        block = match[0]
        header = block.split("\n", 1)[0]
        name = re.search(r'@(?:"([^"\n]+)"|([^\s(]+))\(', header)
        if not name or not any(word in name[0] for word in ("ProbeOperation", "BuildPutWqes", "tilexr_moonep_combine_ht_kernel")):
            continue
        begin, depth, end = name.end(), 1, name.end()
        while end < len(header) and depth:
            depth += (header[end] == "(") - (header[end] == ")")
            end += 1
        parameters = header[begin:end - 1]
        callees = sorted(set(re.findall(r'\bcall\b[^\n]*?@(?:"([^"\n]+)"|([^\s(]+))\(', block)))
        names = [a or b for a, b in callees]
        call_names = [a or b for a, b in re.findall(
            r'\bcall\b[^\n]*?@(?:"([^"\n]+)"|([^\s(]+))\(', block)]
        relevant = Counter(value for value in call_names
                           if re.search(r"vf|simd|vdup|vadd|vst|stack", value, re.I))
        record = {"name": (name[1] or name[2])[:180], "parameter_count": parameter_count(parameters) if depth == 0 else None,
                          "alloca_count": len(re.findall(r"(?m)^\s*%[^=\n]+\s*=\s*alloca\b", block)),
                          "trunc_to_i16_count": len(re.findall(r"\btrunc\b[^\n]*\bto i16\b", block)),
                          "stack_named_operations": sum("stack" in line.lower() for line in block.splitlines()),
                          "relevant_call_counts": {key[:100]: count for key, count in relevant.most_common(6)},
                          "intrinsic_callees": [value[:80] for value in names
                                                if re.search(r"vf|simd|vdup|vst|stack", value, re.I)][:4]}
        records.append((record, header[:550]))
    selected = sorted(records, key=lambda pair: ("vector" not in pair[0]["name"],
                                                 "ProbeOperation" not in pair[0]["name"]))[:2]
    functions = [record for record, _ in selected]
    snippets = [header for _, header in selected]
    triple = re.search(r'(?m)^target triple = "([^"\n]+)"', text)
    attributes = dict(re.findall(r'"([^"\n]*(?:stack|vf)[^"\n]*)"="([^"\n]*)"', text, re.I))
    return {"target_triple": triple[1] if triple else None, "functions": functions,
            "vector_named_definition_count": sum("vector" in record["name"] for record, _ in records),
            "stack_attributes": {key[:60]: value[:60] for key, value in list(attributes.items())[:6]}}, snippets


def inside_container():
    sys.path.insert(0, "/baseline")
    import sst
    root, report = Path("/work"), Path("/work/vf-ir-inspection-1")
    report.mkdir()
    snapshot = json.loads(Path("/baseline/environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    env, compiler = snapshot["settings"], Path(snapshot["settings"]["compiler"]).resolve()
    cann = Path(env["cann_root"])
    out = {"diagnostic_only": True, "simulation_started": False, "compile_only": True,
           "source_modified": False, "header_matches": [], "stack_constants": [], "cases": [], "source_snippets": []}
    roots = [path for path in (cann / "compiler/tikcpp", cann / "aarch64-linux/asc/include") if path.is_dir()]
    assert roots, "installed_header_roots_missing"
    pattern = "__ASC_DISABLE_VF_STACK_RESERVED_UBUF__|VF.*STACK|Vf.*Stack|vf.*stack"
    rg = shutil.which("rg")
    search = ([rg, "-n", "-m", "3", "-g", "*.h", pattern, *map(str, roots)] if rg else
              ["grep", "-r", "-n", "-m", "3", "-E", "--include=*.h", pattern, *map(str, roots)])
    found = subprocess.run(search, capture_output=True, text=True, timeout=30)
    out["header_search_returncode"] = found.returncode
    (report / "vf-stack-header-search.log").write_text(found.stdout + "\nSTDERR\n" + found.stderr)
    seen = set()
    for line in found.stdout.splitlines():
        fields = line.split(":", 2)
        if len(fields) != 3 or not fields[1].isdigit():
            continue
        path, number, code = fields
        if len(out["header_matches"]) < 4:
            out["header_matches"].append({"path": path, "line": int(number)})
        if len(out["source_snippets"]) < 4:
            out["source_snippets"].append(code.strip()[:220])
        if path not in seen and len(seen) < 4:
            seen.add(path)
            lines = Path(path).read_text(errors="replace").splitlines()
            context = "\n".join(lines[max(0, int(number) - 10):int(number) + 12])
            out["stack_constants"].extend({"name": key, "value": value}
                for key, value in re.findall(r"\b(\w*vf\w*stack\w*|\w*stack\w*vf\w*)\s*(?:=|\s)\s*(0x[0-9a-f]+|[0-9]+)", context, re.I))
    out["stack_constants"] = out["stack_constants"][:6]
    disassembler = compiler.parent / "llvm-dis"
    if not disassembler.is_file():
        disassembler = None
    form = None
    for index in (3, 0, 1):
        name, digest = CASES[index]
        binding = object_function(root, name, digest)
        source_dir, case_dir = root / name, report / name
        case_dir.mkdir()
        files = [path for path in source_dir.glob("*") if path.suffix in (".h", ".cpp")]
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        original = json.loads((source_dir / "build.json").read_text())[0]
        assert original[0] == env["compiler"] and original.count("-c") == original.count("-o") == 1
        assert Path(original[original.index("-c") + 1]) == source_dir / "combine_ht.cpp"
        row = {"case": name, "validated_object_sha256": binding["object_sha256"], "attempts": [], "ir": []}
        forms = (form,) if form else ("emit-llvm", "save-temps")
        for mode in forms:
            mode_dir = case_dir / mode
            mode_dir.mkdir()
            command = list(original)
            output = mode_dir / ("device.bc" if mode == "emit-llvm" else "device.o")
            command[command.index("-o") + 1] = str(output)
            command.append("-emit-llvm" if mode == "emit-llvm" else "-save-temps=obj")
            with (case_dir / (mode + ".log")).open("wb") as stream:
                try:
                    result = subprocess.run(sst.shell_command(env, "cann", command), cwd=mode_dir,
                                            stdout=stream, stderr=subprocess.STDOUT, timeout=65)
                    code = result.returncode
                except subprocess.TimeoutExpired:
                    code = 124
            row["attempts"].append({"form": mode, "returncode": code})
            candidates = sorted(path for path in mode_dir.rglob("*") if path.suffix in (".ll", ".bc"))[:2]
            for candidate in candidates:
                if candidate.stat().st_size > 32 * 1024 * 1024:
                    continue
                text_file = candidate
                if candidate.suffix == ".bc":
                    if not disassembler or candidate.read_bytes()[:4] != b"BC\xc0\xde":
                        continue
                    text_file = candidate.with_suffix(candidate.suffix + ".ll")
                    decoded = subprocess.run(sst.shell_command(env, "cann", [str(disassembler), str(candidate), "-o", str(text_file)]),
                                             capture_output=True, timeout=15)
                    if decoded.returncode:
                        continue
                summary, snippets = summarize_ir(text_file.read_text(errors="replace"))
                if summary["functions"]:
                    row["ir"].append({"file": str(text_file.relative_to(report)), **summary})
                    out["source_snippets"].extend(snippets[:max(0, 8 - len(out["source_snippets"]))])
                    break
            if row["ir"]:
                form = mode
                break
        row["source_unchanged"] = before == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        assert row["source_unchanged"], "inspection_modified_source"
        out["cases"].append(row)
        if not row["ir"]:
            row["compiler_diagnostic"] = [line[:220] for line in (case_dir / (row["attempts"][-1]["form"] + ".log")).read_text(errors="replace").splitlines()
                                           if "error:" in line][:2]
            break
    out["ir_available"] = bool(out["cases"] and out["cases"][0]["ir"])
    (root / "vf-ir-result.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, separators=(",", ":")))


def inventory():
    """Describe retained compiler outputs without recompiling or exporting them."""
    report = Path("/work/vf-ir-inspection-1")
    saved = json.loads(Path("/work/vf-ir-result.json").read_text())
    assert saved["source_modified"] is False and saved["simulation_started"] is False
    files = sorted(path for path in report.rglob("*") if path.is_file())
    out = {"read_only": True, "recompiled": False, "simulation_started": False,
           "file_count": len(files), "files": []}
    for path in files[:12]:
        with path.open("rb") as stream:
            magic = stream.read(8)
        kind = ("ELF" if magic.startswith(b"\x7fELF") else
                "LLVM_bitcode" if magic.startswith(b"BC\xc0\xde") else "other")
        out["files"].append({"file": str(path.relative_to(report)), "bytes": path.stat().st_size,
                             "format": kind, "magic_hex": magic.hex()})
    snapshot = json.loads(Path("/baseline/environment.snapshot.json").read_text())
    compiler = Path(snapshot["settings"]["compiler"]).resolve()
    out["llvm_dis_exists"] = (compiler.parent / "llvm-dis").is_file()
    print(json.dumps(out, separators=(",", ":")))


if __name__ == "__main__":
    try:
        if sys.argv[1] == "--inventory":
            inventory()
        elif sys.argv[1] == "--container":
            inside_container()
        else:
            host(json.loads(sys.argv[1]), entry="vf_ir.py", directory="vf-ir-inspection-1", timeout=480)
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error)[:100]}))
        sys.exit(1)
