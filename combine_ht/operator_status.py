"""Read bounded build, process and phase evidence for the continued HT candidate."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

REMOTE = r'''
import hashlib,json,pathlib,re,subprocess
c="tilexr-combine-ht-f71d0252-v11"
p=subprocess.run(["docker","inspect",c],capture_output=True,text=True,timeout=20)
assert p.returncode==0,"container_inspect_failed"
identity=json.loads(p.stdout)[0]
assert identity["Id"]=="0686f95f26f3e703650724ee7e6da7293b11f7e4ce7154d80e6a089a6614d4c9"
work=next(pathlib.Path(m["Source"]) for m in identity["Mounts"] if m["Destination"]=="/work")
assert str(work)=="/var/tmp/tilexr-combine-ht-f71d0252-ef1e-4b5c-b299-c2429ebcfbdf-v11"
p=subprocess.run(["docker","top",c,"-eo","pid,comm,stat"],capture_output=True,text=True,timeout=20)
assert p.returncode==0,"container_top_failed"
out={"ok":True,"read_only":True,"container_id":identity["Id"],"image":identity["Image"],
     "sst_processes":[line.split() for line in p.stdout.splitlines()[1:] if line.split()[1] in ("sst","sstsim.x")]}
def compact(value):
    if isinstance(value,dict):
        return {k:compact(v) for k,v in value.items() if k not in ("model","perf_resources","model_libraries")}
    if isinstance(value,list):return [compact(v) for v in value]
    return value
out["records"]={}
for name in ("revisions.json","progress.json","run-summary-a0840f02-8p.json",
             "cases/8p-bs16-a0840f02-a1/send-only/run.json","cases/8p-bs16-a0840f02-a1/reduce-only/run.json"):
    path=work/name
    out["records"][name]=compact(json.loads(path.read_text())) if path.exists() else {"found":False}
out["logs"]={}
for name in ("build.log","host.log","cases/8p-bs16-a0840f02-a1/send-only/sim.log",
             "cases/8p-bs16-a0840f02-a1/reduce-only/sim.log"):
    path=work/name
    if path.is_file():
        size=path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0,size-2**20));text=f.read().decode(errors="replace")
        out["logs"][name]={"bytes":size,"selected":[x[:450] for x in text.splitlines() if re.search(r"FATAL:|fatal error:|error:|Assertion|Unknown low level|Simulation is complete",x)][:6]}
path=work/"tilexr_moonep_combine_ht_kernel.o"
out["kernel_sha256"]=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
path=work/"build.json"
if path.exists():
    commands=json.loads(path.read_text())
    out["build_defines"]=[x for x in commands[0] if x.startswith("-DTILEXR_")]
print(json.dumps(out))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    assert json.loads(parser.parse_args().args_file.read_text(encoding="utf-8")) == {}
    key = Path(os.environ["USERPROFILE"]) / ".ssh/id_ed25519"
    argv = ["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12", "root@141.61.94.26",
            shlex.join(["python3", "-"])]
    p = subprocess.run(argv, input=REMOTE.encode(), capture_output=True, timeout=90)
    assert p.returncode == 0, "status_transport_exit_" + str(p.returncode)
    result = json.loads(p.stdout)
    local = Path(os.environ["LOCALAPPDATA"]) / "CrossZone/tilexr/f71d0252-ef1e-4b5c-b299-c2429ebcfbdf-v11/rebuild.json"
    if local.exists():
        saved = json.loads(local.read_text(encoding="utf-8"))
        result["green_launcher"] = {k: saved[k] for k in ("ok", "stage", "error_type", "detail", "green_log") if k in saved}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
