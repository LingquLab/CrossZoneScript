"""Find bounded, commit-bound HT validation metadata without rerunning cases."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess

REMOTE = r'''
import json,pathlib,re,subprocess,time
target="211f7e97728781c4d7f01283c46e02872a34998f"
deadline=time.monotonic()+100
roots=set()
for parent in (pathlib.Path("/var/tmp"),pathlib.Path("/root")):
    for pattern in ("tilexr*combine*","tilexr-ht-*","ht-sst-*"):
        for path in parent.glob(pattern):
            if path.is_dir():roots.add(path)
p=subprocess.run(["docker","ps","-a","--format","{{.Names}}"],capture_output=True,text=True,timeout=20)
assert p.returncode==0,"container_directory_failed"
names=[name for name in p.stdout.splitlines() if re.search(r"combine|tilexr.*ht|ht.*sst",name,re.I)][:24]
if names:
    p=subprocess.run(["docker","inspect",*names],capture_output=True,text=True,timeout=20)
    assert p.returncode==0,"project_mount_inspection_failed"
    for container in json.loads(p.stdout):
        for mount in container.get("Mounts",[]):
            if mount.get("Destination")=="/work":roots.add(pathlib.Path(mount["Source"]))
allowed={"candidate","revision","source_revision","source_commit","commit","kernel_sha256","image_id","ranks","rank_size","world","bs","tokens","topk","hidden","dtype","mode","numeric_validation","status","completed","simulated_us","wall_s","sst_wall_s","stage_wall_s","ok","simt_urma_flag","element_bytes","input_dtype","profiling_compiled"}
def selected(node,path=""):
    rows=[]
    if isinstance(node,dict):
        for key,value in node.items():
            if key in allowed and isinstance(value,(str,int,float,bool)):
                rows.append({"field":path+"/"+key,"value":str(value)[:200]})
            elif key in ("compile_command","compiler_flags","defines") and isinstance(value,list):
                rows.append({"field":path+"/"+key,"value":[x[:160] for x in value if isinstance(x,str) and x.startswith(("-D","--cce"))][:12]})
            rows.extend(selected(value,path+"/"+str(key)))
    elif isinstance(node,list):
        for index,value in enumerate(node):rows.extend(selected(value,path+"/"+str(index)))
    return rows
matches=[];scanned=0;limited=False
for root in sorted(roots):
    if not root.is_dir():continue
    pending=[(root,0)]
    while pending and scanned<700 and time.monotonic()<deadline:
        directory,depth=pending.pop()
        try:entries=list(directory.iterdir())
        except OSError:continue
        for path in entries:
            if path.is_dir():
                if depth<4 and path.name not in (".git","candidate","baseline-candidate","tooling","input_bin","bin","dumps","OUTPUT","node_modules","source","TileXR") and not path.name.startswith("previous-"):
                    pending.append((path,depth+1))
                continue
            if path.suffix!=".json" or not re.search(r"revision|source|manifest|result|summary|progress|build|case|request|report|status|checkpoint",path.name,re.I):continue
            try:
                if path.stat().st_size>2*1024*1024:continue
                scanned+=1;text=path.read_text(errors="strict")
                if target not in text and not re.search(r"\b211f7e97\b",text):continue
                data=json.loads(text)
                matches.append({"path":str(path),"modified":path.stat().st_mtime,"fields":selected(data)[:45]})
            except (OSError,ValueError,UnicodeError):continue
    if scanned>=700 or time.monotonic()>=deadline:limited=True;break
matches.sort(key=lambda row:row["modified"],reverse=True)
print(json.dumps({"ok":True,"read_only":True,"target_commit":target,"scanned_root_count":len(roots),"scanned_metadata_files":scanned,"depth_limit":4,"limited":limited,"match_count":len(matches),"matches":matches[:8]}))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    assert json.loads(parser.parse_args().args_file.read_text(encoding="utf-8")) == {}
    key = Path(os.environ["USERPROFILE"]) / ".ssh/id_ed25519"
    argv = ["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12", "root@141.61.94.26", shlex.join(["python3", "-"])]
    p = subprocess.run(argv, input=REMOTE.encode(), capture_output=True, timeout=150)
    assert p.returncode == 0, "metadata_query_failed"
    print(json.dumps(json.loads(p.stdout)))


if __name__ == "__main__":
    main()
