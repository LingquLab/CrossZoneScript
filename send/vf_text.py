"""Export textual LLVM IR from unchanged retained sources, without an SST run."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from vf_inspect import CASES, host, object_function
from vf_ir import summarize_ir

GATHER = ("mte-scalar_store_post_runtime_gather-24",
          "b9cfe94b49ffef820bb5380a189d88a44e2def774730ad0287b439c2199e1468")


def source_hashes(folder):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in folder.iterdir() if path.suffix in (".cpp", ".h")}


def text_command(original, source, destination):
    command = list(original)
    assert command.count("-c") == command.count("-o") == 1
    assert Path(command[command.index("-c") + 1]) == source
    command[command.index("-c")] = "-S"
    command[command.index("-o") + 1] = str(destination)
    command.append("-emit-llvm")
    return command


def inside_container():
    sys.path.insert(0, "/baseline")
    import sst
    root, report = Path("/work"), Path("/work/vf-text-inspection-1")
    report.mkdir()
    snapshot = json.loads(Path("/baseline/environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    env = snapshot["settings"]
    out = {"diagnostic_only": True, "simulation_started": False, "compile_only": True,
           "source_modified": False, "text_ir_available": False, "cases": [], "source_snippets": []}
    for name, digest in (CASES[3], GATHER, CASES[1]):
        binding = object_function(root, name, digest)
        source_dir = root / name
        before = source_hashes(source_dir)
        case_dir = report / name
        case_dir.mkdir()
        original = json.loads((source_dir / "build.json").read_text())[0]
        assert original[0] == env["compiler"]
        output, log = case_dir / "device.ll", case_dir / "compile.log"
        command = text_command(original, source_dir / "combine_ht.cpp", output)
        (case_dir / "command.json").write_text(json.dumps(command, indent=2))
        with log.open("wb") as stream:
            try:
                process = subprocess.run(sst.shell_command(env, "cann", command), cwd=case_dir,
                                         stdout=stream, stderr=subprocess.STDOUT, timeout=65)
                code = process.returncode
            except subprocess.TimeoutExpired:
                code = 124
        unchanged = before == source_hashes(source_dir)
        assert unchanged, "inspection_modified_retained_source"
        row = {"case": name, "validated_object_sha256": binding["object_sha256"],
               "compiler_returncode": code, "source_unchanged": unchanged,
               "output_exists": output.is_file(), "ir_identified": False}
        if output.is_file() and 0 < output.stat().st_size < 32 * 1024 * 1024:
            text = output.read_text(errors="replace")
            summary, snippets = summarize_ir(text)
            row.update(ir_bytes=output.stat().st_size,
                       ir_sha256=hashlib.sha256(output.read_bytes()).hexdigest(), **summary)
            row["ir_identified"] = bool(summary["functions"] and summary["target_triple"])
            row["vf_outline_visible"] = summary["vector_named_definition_count"] > 0
            out["source_snippets"].extend(line[:350] for line in
                snippets[:max(0, 8 - len(out["source_snippets"]))])
        if code or not row["ir_identified"]:
            row["compiler_diagnostic"] = [line[:220] for line in log.read_text(errors="replace").splitlines()
                                          if "error:" in line or "warning:" in line][:2]
        out["cases"].append(row)
        (root / "vf-text-result.json").write_text(json.dumps(out, indent=2))
        if code or not row["ir_identified"]:
            break
    out["text_ir_available"] = (len(out["cases"]) == 3 and
        all(row["compiler_returncode"] == 0 and row["ir_identified"] for row in out["cases"]))
    (root / "vf-text-result.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, separators=(",", ":")))
    return out["text_ir_available"]


if __name__ == "__main__":
    try:
        if sys.argv[1] == "--container":
            sys.exit(0 if inside_container() else 1)
        host(json.loads(sys.argv[1]), entry="vf_text.py", directory="vf-text-inspection-1", timeout=240)
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error)[:100]}))
        sys.exit(1)
