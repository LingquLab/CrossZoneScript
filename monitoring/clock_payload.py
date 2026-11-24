"""Locate the first failed Clock payload slices without exporting tensor data."""
import argparse
import json
from pathlib import Path
import re
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
assert set(args) == {"run_dir", "revision"}
assert re.fullmatch(r"/opt/urma64p-cases\.[A-Za-z0-9]{8}", args["run_dir"])
assert re.fullmatch(r"[0-9a-f]{40}", args["revision"])
runner = "revision = " + repr(args["revision"]) + "\n" + r"""
import contextlib
import importlib
import io
import json
from pathlib import Path
import sys

case = Path("/retained/clock")
scenario = json.loads((case / "scenario.json").read_text())
assert scenario["candidate_revision"] == revision
sys.path.insert(0, str(case))
case_tools = importlib.import_module("case_tools")
validation = importlib.import_module("full_validation")
require = validation.require
result = {}
def observe(condition, message):
    if not condition and 'Actual kernel output differs' in message:
        frame = sys._getframe(1)
        actual = frame.f_locals["output"][64:-64]
        expected = frame.f_locals["oracle"]
        bpp = frame.f_locals["bpp"]
        result["rank"] = frame.f_locals["r"]
        result["bytes_per_peer"] = bpp
        result["record_keys"] = sorted(frame.f_locals["record"])
        result["peers"] = []
        for peer in range(len(expected) // bpp):
            begin = peer * bpp
            out, gold = actual[begin:begin+bpp], expected[begin:begin+bpp]
            offsets = [i for i,(a,b) in enumerate(zip(out,gold)) if a != b]
            if offsets:
                result["peers"].append(dict(source=peer, count=len(offsets), first=offsets[0],
                                            last=offsets[-1], stripe_count=len(set(i//512 for i in offsets)),
                                            all_zero=not any(out)))
    return require(condition, message)
validation.require = observe
with contextlib.redirect_stdout(io.StringIO()):
    try:
        case_tools.check_outputs(case, scenario, case / "bin")
    except (AssertionError, ValueError) as error:
        result["validation_error"] = str(error)[:220]
assert result.get("peers"), "saved failure could not be localized"
print(json.dumps(result))
"""
image = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
origin = "/home/h30059441/tilexr_sim_cc9_hapi_work"
generator = "/work/coord_runner/full-matrix-fdcf9d52-20261119/generator"
commands = ["docker", "run", "--rm", "-i", "--network", "none", "-v", origin + ":/work:ro",
            "-v", args["run_dir"] + ":/retained:ro", "-e", "PYTHONPATH=" + generator,
            "--entrypoint", "python3", image, "-Bu", "-"]
host = f"import subprocess,sys\ncall=subprocess.run({commands!r},input={runner.encode()!r},capture_output=True,timeout=135)\nsys.stdout.buffer.write(call.stdout)\nsys.stderr.buffer.write(call.stderr)\nsys.exit(call.returncode)\n"
ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
       "-o", "IdentitiesOnly=yes", "-i", str(Path.home() / ".ssh/id_ed25519_server"),
       "root@141.61.94.30", "python3 -u -"]
result = subprocess.run(ssh, input=host.encode(), capture_output=True, timeout=155)
if result.returncode:
    error = result.stderr.decode("utf-8", "replace").splitlines()
    messages = [line[:200] for line in error if re.match(r"^(?:[A-Za-z]+Error:|ssh:)",line)]
    print(json.dumps(dict(ssh_exit=result.returncode, errors=messages[-4:])))
else:
    print(json.dumps(json.loads(result.stdout)))
raise SystemExit(result.returncode)
