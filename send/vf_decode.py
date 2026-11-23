"""Inspect installed Gather lowering and decode bounded retained LLVM bitcode."""
import bisect
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from vf_inspect import CASES, host, object_function
from vf_ir import summarize_ir
from vf_text import GATHER, source_hashes


def gather_contract(path):
    assert 0 < path.stat().st_size < 16000000
    text = path.read_text(errors="replace")
    directives = list(re.finditer(r'(?m)^#\s+[0-9]+\s+"([^"\n]+)"', text))
    offsets = [match.start() for match in directives]
    records, snippets = [], []
    for match in re.finditer(r"\b(DataCopyGatherImpl)\s*\(", text):
        index = bisect.bisect_right(offsets, match.start()) - 1
        source = directives[index][1] if index >= 0 else "unknown"
        if "/dav_3510/" not in source:
            continue
        brace, semicolon = text.find("{", match.end()), text.find(";", match.end())
        if brace < 0 or brace - match.end() > 1600 or (semicolon >= 0 and semicolon < brace):
            continue
        depth, end = 1, brace + 1
        while end < len(text) and depth and end - brace < 20000:
            depth += (text[end] == "{") - (text[end] == "}")
            end += 1
        if depth:
            continue
        signature = " ".join(text[text.rfind("\n", 0, match.start()) + 1:brace].split())
        body = text[brace + 1:end - 1]
        calls = sorted(set(re.findall(r"\b([A-Za-z_]\w*)\s*(?:<[^;\n]{1,180}>)?\s*\(", body)))
        records.append({"name": match[1], "source": source, "callees": calls[:18]})
        if not snippets:
            snippets.append(signature[:400])
            relevant = [line.strip() for line in body.splitlines()
                        if re.search(r"gather|ShiftLeft|srcAddr|baseAddr|offset", line, re.I)]
            snippets.extend(line[:350] for line in relevant[:3])
        if len(records) == 2:
            break
    return {"preprocess_bytes": path.stat().st_size, "definitions": records}, snippets


def inside_container():
    sys.path.insert(0, "/baseline")
    import sst
    root, report = Path("/work"), Path("/work/vf-decode-inspection-1")
    report.mkdir()
    snapshot = json.loads(Path("/baseline/environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    env = snapshot["settings"]
    compiler = Path(env["compiler"]).resolve()
    out = {"diagnostic_only": True, "simulation_started": False, "source_modified": False,
           "ir_available": False, "cases": [], "source_snippets": []}
    saved = root / "vf-ir-inspection-1" / CASES[3][0]
    preprocessed = list((saved / "save-temps").glob("*.ccei"))
    assert len(preprocessed) == 1
    out["gather_contract"], snippets = gather_contract(preprocessed[0])
    out["source_snippets"].extend(snippets)

    def run(label, command, timeout):
        log = report / (label + ".log")
        with log.open("wb") as stream:
            try:
                process = subprocess.run(sst.shell_command(env, "cann", command),
                                         stdout=stream, stderr=subprocess.STDOUT, cwd=report, timeout=timeout)
                code = process.returncode
            except subprocess.TimeoutExpired:
                code = 124
        return code, log

    located = shutil.which("llvm-dis")
    decoder = compiler.parent / "llvm-dis"
    if not decoder.is_file() and located:
        decoder = Path(located)
    if decoder.is_file():
        method = "llvm-dis"
        decode_command = lambda source, output: [str(decoder), str(source), "-o", str(output)]
    else:
        method = "compiler-cc1"
        code, log = run("cc1-help", [str(compiler), "-cc1", "--help"], 15)
        help_text = log.read_text(errors="replace")
        out["cc1_help_returncode"] = code
        out["cc1_emit_llvm_option_found"] = bool(re.search(r"(?m)^\s*-emit-llvm\s", help_text))
        decode_command = lambda source, output: [str(compiler), "-cc1", "-triple", "hiipu64-hisilicon-cce",
                                                "-x", "ir", "-emit-llvm", str(source), "-o", str(output)]
    out["decode_method"] = method
    ready = method == "llvm-dis" or (out["cc1_help_returncode"] == 0 and out["cc1_emit_llvm_option_found"])
    if ready:
        for index, (name, digest) in enumerate((CASES[3], GATHER, CASES[1])):
            binding = object_function(root, name, digest)
            source_dir, case_dir = root / name, report / name
            before = source_hashes(source_dir)
            case_dir.mkdir()
            bitcode = saved / "emit-llvm/device.bc" if index == 0 else case_dir / "device.bc"
            row = {"case": name, "validated_object_sha256": binding["object_sha256"],
                   "reused_bitcode": index == 0, "ir_identified": False}
            if index:
                command = json.loads((source_dir / "build.json").read_text())[0]
                assert command[0] == env["compiler"] and command.count("-c") == command.count("-o") == 1
                assert Path(command[command.index("-c") + 1]) == source_dir / "combine_ht.cpp"
                command[command.index("-o") + 1] = str(bitcode)
                command.append("-emit-llvm")
                row["compile_returncode"], compile_log = run("compile-" + str(index), command, 65)
                if row["compile_returncode"]:
                    row["compiler_diagnostic"] = [line[:220] for line in compile_log.read_text(errors="replace").splitlines()
                                                   if "error:" in line or "warning:" in line][:2]
            if bitcode.is_file():
                assert 0 < bitcode.stat().st_size < 32 * 1024 * 1024
                blob = bitcode.read_bytes()
                row["bitcode_sha256"] = hashlib.sha256(blob).hexdigest()
                if blob[:4] == b"BC\xc0\xde":
                    output = case_dir / "decoded.ll"
                    row["decode_returncode"], log = run("decode-" + str(index), decode_command(bitcode, output), 30)
                    if output.is_file() and 0 < output.stat().st_size < 32 * 1024 * 1024:
                        summary, snippets = summarize_ir(output.read_text(errors="replace"))
                        summary["functions"] = summary["functions"][:1]
                        row.update(ir_bytes=output.stat().st_size, **summary)
                        row["ir_identified"] = bool(summary["functions"] and summary["target_triple"])
                        out["source_snippets"].extend(line[:350] for line in snippets[:1]
                            if len(out["source_snippets"]) < 8)
                    if row["decode_returncode"]:
                        row["decode_diagnostic"] = [line[:220] for line in log.read_text(errors="replace").splitlines()
                                                     if "error:" in line or "warning:" in line][:2]
            row["source_unchanged"] = before == source_hashes(source_dir)
            assert row["source_unchanged"]
            out["cases"].append(row)
            (root / "vf-decode-result.json").write_text(json.dumps(out, indent=2))
            if not row["ir_identified"] or row.get("compile_returncode", 0) or row.get("decode_returncode", 1):
                break
    out["ir_available"] = len(out["cases"]) == 3 and all(row["ir_identified"] for row in out["cases"])
    (root / "vf-decode-result.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, separators=(",", ":")))


if __name__ == "__main__":
    try:
        if sys.argv[1] == "--container":
            inside_container()
        else:
            host(json.loads(sys.argv[1]), entry="vf_decode.py", directory="vf-decode-inspection-1", timeout=300)
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error)[:100]}))
        sys.exit(1)
