"""Report whether the retained SST platform accepts sequential kernels per chip."""
import json
from pathlib import Path
import subprocess
import sys

workspace = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(workspace / "ht_t32_e2e"), str(workspace / "alltoall")]
from green_auth import askpass_environment
from ssh_transport import HOST

source = r'''
import ast
import json
from pathlib import Path
import re
import subprocess
template = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-sst-mte-bulk-forward-hotpath-1a2f3549")
snapshot = json.loads((template / "environment.snapshot.json").read_text())
workload = json.loads((template / "workload.yml").read_text())["workload_config"]
image = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
code = """
import json
import re
import sys
from pathlib import Path
root = Path(sys.argv[1]) / 'v310_deployment'
summary = []
fields = r'\b(vec_func|kernel_file|workload_config|kernel_param|kernel_list|kernel_sequence|op_list|multi_kernel)\b'
for path in root.rglob('*.py'):
    lines = [(i, line.strip()) for i,line in enumerate(path.read_text(errors='replace').splitlines(),1)
             if re.search(fields,line)]
    if lines:
        summary.append([path.name, [i for i,line in lines[:20]], sorted(set(re.findall(fields,' '.join(line for i,line in lines))))])
print(json.dumps(summary[:20]))
"""
p = subprocess.run(["docker","run","--rm","--runtime=runc","--network=none","--cap-drop=ALL",image,
                    "python3","-c",code,snapshot["settings"]["simulator_root"]],
                   check=False,capture_output=True,timeout=65)
print(json.dumps({"workload_count":len(workload),
                  "chip_entry_type":type(next(iter(workload.values()))["vec_func"]).__name__,
                  "chip_kernel_file_type":type(next(iter(workload.values()))["kernel_file"]).__name__,
                  "model_scan_exit":p.returncode,
                  "model_field_sites":json.loads(p.stdout) if p.returncode == 0 else []}))
'''
ssh = ["ssh", "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
       "-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1",
       "-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no", HOST, "python3", "-"]
with askpass_environment() as environment:
    proc = subprocess.run(ssh, input=source.encode(), env=environment, capture_output=True, timeout=85)
if proc.returncode:
    print(json.dumps({"ssh_exit":proc.returncode,"stderr_bytes":len(proc.stderr)}))
else:
    print(json.dumps(json.loads(proc.stdout),separators=(",",":")))
