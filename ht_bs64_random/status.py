"""Read bounded SST stage status for the single BS64 random Combine case."""
import json
from pathlib import Path
import subprocess
import sys

workspace = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(workspace / "ht_t32_e2e"), str(workspace / "alltoall")]
from green_auth import askpass_environment
from ssh_transport import HOST

source = r'''
import json,re,time
from pathlib import Path
base=Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native")
result={}
for stage in ("local-send","local-reduce","inter-send","final-reduce"):
    case=base/("ht-bs64-random-20261124-"+stage)
    if not case.is_dir():
        continue
    info={}
    for name in ("run.json","phase-summary.json"):
        path=case/name
        if path.is_file():
            data=json.loads(path.read_text())
            info.update({k:data[k] for k in ("status","returncode","cqe","sim_us","checked","first_mismatch","kernel_sha256") if k in data})
    log=case/"sim.log"
    if log.is_file():
        raw=log.read_bytes()
        info.update(cqe=raw.count(b"WrCQEResp"),log_age=round(time.time()-log.stat().st_mtime),
                    completion=b"Simulation is complete" in raw,
                    error_counts={k:raw.lower().count(k.encode()) for k in (
                        "segmentation fault","mmio","unsupported","fatal","assertion") if k.encode() in raw.lower()})
    result[stage]=info
print(json.dumps(result))
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
