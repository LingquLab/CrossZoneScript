"""Read the retained local HT case only."""
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from green_auth import askpass_environment

case = "/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-local-ht-fp32-9f0f0f9a"
support = Path(__file__).resolve().parents[2] / "inter_server"
common = (support / "owner_trace_remote.py").read_text().split("def doorbell_coordinates(",1)[0]
reader = "def read_observed(" + (support / "diagnose_t96_sq_remote.py").read_text().split(
    "def read_observed(",1)[1].split("def diagnose_t96(",1)[0]
code = common + reader + f"\ncase = {case!r}\n" + r"""
import json,re,time
from pathlib import Path
p=Path(case)
results={"exists":p.is_dir()}
for f in ("run.json","task-result.json","e2e-summary.json"):
    x=p/f
    if x.is_file():
        data=json.loads(x.read_bytes())
        results[f]={k:data[k] for k in ("status","returncode","run","runner_exit_code","runner_wall_seconds","executed_sst","sst_simulated_microseconds","guards","checked","cqe_log_records","stage_error","first_mismatch") if k in data}
for f in ("sim.log","e2e-failure.log","check.log","native-runner.log"):
    x=p/f
    if x.is_file():
        raw=x.read_bytes()
        results[f]={"bytes":len(raw),"age_seconds":round(time.time()-x.stat().st_mtime),
                    "cqe":raw.count(b"WrCQEResp")}
        matches=re.findall(rb"Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)",raw)
        if matches:
            results[f]["completion"]=[v.decode() for v in matches[-1]]
        causes={"mmio":b"MMIO","sq_db":b"DB addr","segfault":b"Segmentation fault","cqe_timeout":b"CQ_TIMEOUT","failed":b"failed","fatal":b"fatal","assert":b"AssertionError"}
        results[f]["categories"]={k:raw.lower().count(v.lower()) for k,v in causes.items() if v.lower() in raw.lower()}
        allowed=re.findall(rb"(?:ValueError|AssertionError): (rank[0-9]{2} (?:returned|completed|output|status)\[[0-9]+\] mismatch: [0-9a-f]+ != [0-9a-f]+)",raw)
        if allowed:
            results[f]["first_mismatch"]=allowed[-1].decode()
if p.is_dir():
    missing, sampled = [], 0
    for host in range(64):
        path=p/"OUTPUT"/"stat_trace"/f"Host{host}_ba_layer_trace.json"
        if not path.is_file():
            continue
        sampled += 1
        events,truncated=read_observed(path,host)
        if truncated:
            continue
        for peer in range(host//8*8,host//8*8+8):
            if peer==host:
                continue
            qp=peer%8
            ring=peer*40+(qp%2)*20+qp//2
            cq=events["WR_REQ"].get(ring,set())
            if len(cq.intersection(range(5)))!=5:
                missing.append([host,peer,sorted(set(range(5))-cq)])
    results["ba_trace"]={"sampled":sampled,"missing_cq_qps":len(missing),"sample":missing[:24]}
    log=p/"sim.log"
    if log.is_file():
        found=[]
        for line in log.read_bytes().splitlines():
            if b"WrCQEResp" in line:
                m=re.search(rb"\bSim\s*[:=]\s*(\d+)",line)
                if m: found.append(int(m[1]))
        if found:
            results["last_cqe_sim"]=max(found)
print(json.dumps(results,separators=(",",":")))
"""
ssh = ["ssh", "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", "-o", "BatchMode=no",
       "-o", "NumberOfPasswordPrompts=1", "-o", "PreferredAuthentications=password",
       "-o", "PubkeyAuthentication=no", "root@141.61.94.58", "python3", "-"]
with askpass_environment() as env:
    proc = subprocess.run(ssh,input=code.encode(),env=env,capture_output=True,timeout=50)
    if proc.returncode:
        print(json.dumps({"ssh_exit":proc.returncode,"stderr_bytes":len(proc.stderr)}))
    else:
        print(json.dumps(json.loads(proc.stdout),separators=(",",":")))
