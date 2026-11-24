"""Run exact shared-platform controls on the already authorized CPU SST host."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--args-file",type=Path,required=True)
    args=json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    assert args in ({},{"status":True})
    key=Path(os.environ["USERPROFILE"])/".ssh/id_ed25519"
    ssh=["ssh","-T","-i",str(key),"-o","BatchMode=yes","-o","IdentitiesOnly=yes","-o","StrictHostKeyChecking=yes","-o","ConnectTimeout=12","root@141.61.94.26"]
    def remote(argv,payload=None,timeout=30):
        return subprocess.run(ssh+[shlex.join(argv)],input=payload,capture_output=True,timeout=timeout)
    name="tilexr-combine-ht-f71d0252-v11"
    p=remote(["docker","inspect",name]);assert p.returncode==0
    identity=json.loads(p.stdout)[0]
    assert identity["Id"]=="0686f95f26f3e703650724ee7e6da7293b11f7e4ce7154d80e6a089a6614d4c9"
    assert identity["Image"]=="sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f"
    assert identity["HostConfig"]["NetworkMode"]=="none"
    p=remote(["docker","top",name,"-eo","pid,comm"])
    assert p.returncode==0 and not any(line.split()[-1] in ("sst","sstsim.x") for line in p.stdout.decode().splitlines()[1:])
    root="/var/tmp/tilexr-combine-ht-f71d0252-ef1e-4b5c-b299-c2429ebcfbdf-v11/diagnostics/verified-platform-a1"
    if args.get("status"):
        p=remote(["cat",root+"/summary.json"]);assert p.returncode==0
        print(json.dumps(json.loads(p.stdout)));return
    assert remote(["mkdir","-m","700",root]).returncode==0
    filename="verified_platform_host.py"
    assert remote(["bash","-c",'umask 077; set -C; cat > "$1"',"upload",root+"/"+filename],Path(__file__).with_name(filename).read_bytes()).returncode==0
    p=remote(["python3",root+"/"+filename],timeout=1400)
    if p.stdout:print(json.dumps(json.loads(p.stdout)))
    raise SystemExit(0 if p.returncode==0 else 1)


if __name__=="__main__":main()
