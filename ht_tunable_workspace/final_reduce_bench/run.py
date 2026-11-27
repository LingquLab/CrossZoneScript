"""Run the FinalReduce timing matrix through the existing green SST bridge."""
import base64
import json
from pathlib import Path
import re
import subprocess
import sys

here = Path(__file__).resolve().parent
root = here.parents[1]
sys.path[:0] = [str(root / "ht_t32_e2e"), str(root / "alltoall")]
from green_auth import askpass_environment
from ssh_transport import HOST

options = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ["--args-file"] else {}
label = options.get("label", "final-reduce-bench")
assert re.fullmatch(r"final-reduce-bench(?:-[a-z0-9-]+)?", label)
fix = json.loads((root / "combine_cast/results/v310-cast-library-fix.json").read_text())
decoder = fix["library_source"]["result"]["libs"]["libDavinciSimulator.so"]
exports = (root / "ht_t32_e2e/exports.py").read_text().split("\ndef export_actual", 1)[0]
exports = exports.replace("6d4cd3b729177646ca1c43d57971e5266a946405b6f820f3d0d914114b7ccfce", decoder)
request = dict(image=fix["regression"]["result"]["image"],
               object_sha256=json.loads((here / "object.json").read_text())["object_sha256"],
               object=base64.b64encode((here / "probe.o.gz").read_bytes()).decode(),
               case=(here / "case.py").read_text(), exports=exports)
request.update(json.loads((here / "object.json").read_text()))
request["tokens"] = options.get("tokens", [32, 128, 512])
abi = json.loads((here / "abi.json").read_text())
request["abi"] = {str(tokens): abi[str(tokens)] for tokens in request["tokens"]}
runtime = (root / "combine_cast/fix_run_remote.py").read_text().split("\ndef extract_inputs", 1)[0]
runtime = runtime.replace('SUITE = PATCH / OPTIONS.get("run_name", "regression")', f"SUITE = PATCH / {label!r}")
runtime = runtime.replace('re.fullmatch(r"regression(?:-[a-z0-9]+)?", SUITE.name)',
                          're.fullmatch(r"final-reduce-bench(?:-[a-z0-9-]+)?", SUITE.name)')
source = "\n".join(("REQUEST=" + repr(request), runtime, (here / "remote.py").read_text()))
compile(source, "<v310-final-reduce-bench>", "exec")
if options.get("check_only"):
    print(json.dumps({"source_bytes": len(source), "object_sha256": request["object_sha256"]}))
    raise SystemExit(0)
ssh = ["ssh", "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
       "-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1", "-o",
       "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no", HOST, "python3", "-"]
with askpass_environment() as env:
    result = subprocess.run(ssh, input=source.encode(), env=env, capture_output=True, timeout=1000)
if result.returncode:
    Path("final-reduce-bench-ssh.log").write_bytes(result.stderr)
    print(json.dumps({"ssh_exit": result.returncode, "stderr_bytes": len(result.stderr)}))
else:
    print(json.dumps(json.loads(result.stdout), separators=(",", ":")))
