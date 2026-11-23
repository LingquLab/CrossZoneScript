"""Check the generated VF interface before simulating a predicate-mask trial."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
from vf_ir import summarize_ir


def inspect(folder, env, sst):
    folder = Path(folder)
    assert folder.parent == Path("/work") and folder.name in (
        "mte-interleave_predicate_runtime-28", "mte-interleave_vf_packed-32")
    report = folder / "ir-check"
    report.mkdir()
    inputs = [path for path in folder.iterdir() if path.suffix in (".cpp", ".h")]
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    command = json.loads((folder / "build.json").read_text())[0]
    assert command[0] == env["compiler"] and command.count("-c") == command.count("-o") == 1
    assert Path(command[command.index("-c") + 1]) == folder / "combine_ht.cpp"
    bitcode, output = report / "device.bc", report / "decoded.ll"
    command[command.index("-o") + 1] = str(bitcode)
    command.append("-emit-llvm")
    out = {"simulation_started": False, "source_unchanged": False, "identified": False}
    commands = (("compile", command, 65),
                ("decode", [env["compiler"], "-cc1", "-triple", "hiipu64-hisilicon-cce", "-x", "ir",
                            "-emit-llvm", str(bitcode), "-o", str(output)], 30))
    for name, argv, timeout in commands:
        log = report / (name + ".log")
        with log.open("wb") as stream:
            try:
                process = subprocess.run(sst.shell_command(env, "cann", argv),
                                         stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
                code = process.returncode
            except subprocess.TimeoutExpired:
                code = 124
        out[name + "_returncode"] = code
        if code:
            out["diagnostic"] = [line[:220] for line in log.read_text(errors="replace").splitlines()
                                 if "error:" in line or "warning:" in line][:2]
            break
    out["source_unchanged"] = before == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    assert out["source_unchanged"]
    if output.is_file() and 0 < output.stat().st_size < 32 * 1024 * 1024:
        text = output.read_text()
        summary, snippets = summarize_ir(text)
        function = next((item for item in summary["functions"] if re.search(r"BuildPutWqes(?:Packed)?ILb0", item["name"])), None)
        if function:
            header = next(line for line in text.splitlines() if line.startswith("define ") and "@" + function["name"] + "(" in line)
            out.update(identified=True, function=function,
                       generic_pointer_parameter_count=len(re.findall(r"\bptr(?!\s+addrspace\b)", header)),
                       source_snippets=[header[:450]])
    (folder / "ir-check.json").write_text(json.dumps(out, indent=2))
    return out
