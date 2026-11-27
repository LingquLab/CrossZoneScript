"""Run the two isolated worker probes on the Cast-fixed SST image."""
import base64
import json
from pathlib import Path
import subprocess
import sys

here = Path(__file__).resolve().parent
root = here.parents[1]
sys.path[:0] = [str(root / "ht_t32_e2e"), str(root / "alltoall")]
from green_auth import askpass_environment
from ssh_transport import HOST

options = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ["--args-file"] else {}
trace = bool(options.get("trace"))
artifact = json.loads((here / ("trace-object.json" if trace else "object.json")).read_text())
fix = json.loads((root / "combine_cast/results/v310-cast-library-fix.json").read_text())
image = fix["regression"]["result"]["image"]
decoder = fix["library_source"]["result"]["libs"]["libDavinciSimulator.so"]
parameters = dict(options=options, artifact=artifact, image=image, decoder=decoder,
                  object=base64.b64encode((here / ("trace.o.gz" if trace else "probe.o.gz")).read_bytes()).decode())
runtime = (root / "combine_cast/fix_run_remote.py").read_text()
runtime = runtime[:runtime.index("def extract_inputs")].replace(
    "SUITE = PATCH / OPTIONS.get(\"run_name\", \"regression\")",
    'SUITE = PATCH / ("reduce-probe-" + OPTIONS.get("label", "rank0"))').replace(
    're.fullmatch(r"regression(?:-[a-z0-9]+)?", SUITE.name)',
    're.fullmatch(r"reduce-probe-[a-z0-9-]+", SUITE.name)')
exports = (root / "ht_t32_e2e/exports.py").read_text().split("\ndef export_actual", 1)[0]
exports = exports.replace("6d4cd3b729177646ca1c43d57971e5266a946405b6f820f3d0d914114b7ccfce", decoder)
decoder_code = exports + (here / "check.py").read_text()
source = '\n'.join(("REQUEST=" + repr(parameters), "OPTIONS=REQUEST['options']", "CHECKER=" + repr(decoder_code),
                    runtime, (here / "remote.py").read_text()))
compile(source, "<isolated-bf16-reduction>", "exec")
if options.get("check_only"):
    print(json.dumps({"source_bytes": len(source), "object": artifact}))
    raise SystemExit
ssh = ["ssh", "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
       "-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1", "-o",
       "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no", HOST, "python3", "-"]
with askpass_environment() as env:
    result = subprocess.run(ssh, input=source.encode(), env=env, capture_output=True, timeout=620)
if result.returncode:
    Path("reduce-probe-ssh.log").write_bytes(result.stderr)
    print(json.dumps({"ssh_exit": result.returncode, "stderr_bytes": len(result.stderr)}))
else:
    print(json.dumps(json.loads(result.stdout), separators=(",", ":")))
