"""Read the documented passed platform using its existing green-local SSH login."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

REMOTE = r'''
import hashlib,json,pathlib,subprocess
root=pathlib.Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native")
out={"read_only":True,"root_exists":root.is_dir(),"images":{},"passed_cases":[],"template":{},"sst_running":False}
for image in ("sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5", "sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f"):
    p=subprocess.run(["docker","image","inspect","--format","{{.Id}}",image],capture_output=True,text=True,timeout=10)
    out["images"][image]=p.returncode==0
p=subprocess.run(["pgrep","-x","sstsim.x"],capture_output=True,timeout=5)
out["sst_running"]=p.returncode==0
def selected(path):
    data=json.loads(path.read_text())
    return {key:data[key] for key in ("status","returncode","kernel_sha256","candidate_commit","sst_simulated_microseconds","runner_wall_seconds","cqe_log_records") if key in data}
for path in sorted(root.glob("*local-ht*"))[:32]:
    manifest=path/"case-manifest.json"
    record=path/"run.json"
    if not manifest.is_file() or not record.is_file():continue
    data=selected(manifest)
    if data.get("kernel_sha256")!="09b138b368c80227aa4d0a0a5dbf56d135f325044352f7608a0a8765191c9966":continue
    case={"path":str(path),"manifest":data,"run":selected(record)}
    for name in ("e2e-summary.json","blue-build-record.json"):
        if (path/name).is_file():case[name]=selected(path/name)
    out["passed_cases"].append(case)
    if len(out["passed_cases"])>=3:break
template=root/"t4-sst-mte-bulk-forward-hotpath-1a2f3549"
out["template"]={"path":str(template),"exists":template.is_dir(),"fields":[],"config_hashes":{}}
if (template/"run.json").is_file():out["template"]["run"]=selected(template/"run.json")
try:
    import yaml
    keys={"component","pv_mode","agent_mode","lsu_level","enable_latency_model","simt_urma_flag","urma_flag","V310_flag","UBusSim_flag","host_ba_mem_wr_bond_mode_flag"}
    def fields(node,prefix):
        if isinstance(node,dict):
            for key,value in node.items():
                path=prefix+"/"+str(key)
                if key in keys and not isinstance(value,(dict,list)) and (key!="component" or any("/"+part+"/" in path for part in ("su","smmu","biu","aa","udie_aa"))):
                    yield {"path":path,"value":value}
                else:yield from fields(value,path)
        elif isinstance(node,list):
            for index,value in enumerate(node):yield from fields(value,prefix+"/"+str(index))
    for directory in ("v310_config","UBusSim_config"):
        for path in sorted((template/directory).glob("*.yml")):
            if path.name=="workload_config.yml":continue
            rel=str(path.relative_to(template))
            out["template"]["config_hashes"][rel]=hashlib.sha256(path.read_bytes()).hexdigest()
            out["template"]["fields"].extend(fields(yaml.safe_load(path.read_text()),rel))
    out["template"]["fields"]=out["template"]["fields"][:45]
except ImportError:
    out["template"]["yaml_available"]=False
print(json.dumps(out))
'''


@contextmanager
def login_environment():
    directory = Path("D:/project/仿真配置")
    note = next((directory / name for name in ("仿真系统环境信息.md", "仿真系统环境信息")
                 if (directory / name).is_file()), None)
    if note is None:
        raise FileNotFoundError("documented_green_login_unavailable")
    with tempfile.TemporaryDirectory(prefix="ht-reference-login-") as temporary:
        reader = Path(temporary) / "read_login.py"
        helper = Path(temporary) / "askpass.sh"
        reader.write_text(
            "from pathlib import Path\nimport re,sys\n"
            "raw=Path(sys.argv[1]).read_bytes()\n"
            "try: text=raw.decode('utf-8-sig')\n"
            "except UnicodeDecodeError: text=raw.decode('gb18030')\n"
            "match=re.search(r'密码更新[：:]\\s*([^\\r\\n]+)',text)\n"
            "if match is None or not match.group(1).strip(): sys.exit(1)\n"
            "sys.stdout.write(match.group(1).strip()+'\\n')\n", encoding="utf-8")
        helper.write_text("#!/bin/sh\nexec " + shlex.join((Path(sys.executable).as_posix(), reader.as_posix(), note.as_posix())) + "\n",
                          encoding="utf-8", newline="\n")
        helper.chmod(0o700)
        environment = os.environ.copy()
        environment.update(SSH_ASKPASS=helper.as_posix(), SSH_ASKPASS_REQUIRE="force", DISPLAY="cross-zone:0")
        yield environment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    assert args in ({}, {"shared_on_current_host": True})
    if args.get("shared_on_current_host"):
        key = Path(os.environ["USERPROFILE"]) / ".ssh/id_ed25519"
        argv = ["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12", "root@141.61.94.26",
                shlex.join(["python3", "-"])]
        result = subprocess.run(argv, input=REMOTE.encode(), capture_output=True, timeout=90)
        assert result.returncode == 0, "shared_platform_inspection_failed"
        print(json.dumps(json.loads(result.stdout)))
        return 0
    argv = ["ssh", "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12",
            "-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1", "-o", "PreferredAuthentications=password",
            "-o", "PubkeyAuthentication=no", "root@141.61.94.58", shlex.join(["python3", "-"])]
    try:
        with login_environment() as environment:
            result = subprocess.run(argv, input=REMOTE.encode(), env=environment, capture_output=True, timeout=90)
        if result.returncode == 0:
            print(json.dumps(json.loads(result.stdout)))
        else:
            print(json.dumps({"read_only": True, "accessible": False, "ssh_exit": result.returncode}))
        return 0 if result.returncode == 0 else 1
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"read_only": True, "accessible": False, "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
