"""Run immutable-object QP-registration controls in the owned V1.1 container."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

REFERENCE = r'''
import hashlib,json,pathlib,subprocess
root=pathlib.Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native")
out={"read_only":True,"root_exists":root.is_dir(),"cases":[],"images":{}}
for image in ("sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5", "sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f"):
    p=subprocess.run(["docker","image","inspect","--format","{{.Id}}",image],capture_output=True,text=True,timeout=10)
    out["images"][image]=p.returncode==0
paths=[root/"t4-sst-mte-bulk-forward-hotpath-1a2f3549"]
if root.is_dir():paths.extend(sorted(root.glob("*local-ht*09b138b3*"))[:4])
for path in paths:
    case={"path":str(path),"exists":path.is_dir(),"records":{},"config_hashes":{}}
    for name in ("run.json","e2e-summary.json","blue-build-record.json","case-manifest.json"):
        p=path/name
        if not p.is_file() or p.stat().st_size>4*1024*1024:continue
        data=json.loads(p.read_text())
        case["records"][name]={key:data[key] for key in ("status","returncode","kernel_sha256","candidate_commit","sst_simulated_microseconds","runner_wall_seconds","cqe_log_records") if key in data}
    for directory in ("v310_config","UBusSim_config"):
        for p in sorted((path/directory).glob("*.yml")):
            if p.name!="workload_config.yml":case["config_hashes"][str(p.relative_to(path))]=hashlib.sha256(p.read_bytes()).hexdigest()
    out["cases"].append(case)
print(json.dumps(out))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    assert args in ({}, {"status": True})
    key = Path(os.environ["USERPROFILE"]) / ".ssh/id_ed25519"
    ssh = ["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
           "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12", "root@141.61.94.26"]

    def remote(argv, payload=None, timeout=30):
        return subprocess.run(ssh + [shlex.join(argv)], input=payload, capture_output=True, timeout=timeout)

    container = "tilexr-combine-ht-f71d0252-v11"
    result = remote(["docker", "inspect", container])
    assert result.returncode == 0
    identity = json.loads(result.stdout)[0]
    assert identity["Id"] == "0686f95f26f3e703650724ee7e6da7293b11f7e4ce7154d80e6a089a6614d4c9"
    assert identity["Image"] == "sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f"
    assert identity["HostConfig"]["NetworkMode"] == "none"
    assert identity["Config"]["Labels"]["crosszone.session"] == "f71d0252-ef1e-4b5c-b299-c2429ebcfbdf"
    result = remote(["docker", "top", container, "-eo", "pid,comm"])
    assert result.returncode == 0 and not any(line.split()[-1] in ("sst", "sstsim.x") for line in result.stdout.decode().splitlines()[1:])
    work = "/var/tmp/tilexr-combine-ht-f71d0252-ef1e-4b5c-b299-c2429ebcfbdf-v11"
    if args.get("status"):
        result = remote(["cat", work + "/diagnostics/fixture-vs-model-qp-a1/summary.json"])
        assert result.returncode == 0
        print(json.dumps(json.loads(result.stdout)))
        return 0
    result = remote(["sha256sum", work + "/sst.py"])
    assert result.returncode == 0 and result.stdout.decode().split()[0] == hashlib.sha256(Path(__file__).with_name("sst.py").read_bytes()).hexdigest()
    directory = work + "/diagnostics/fixture-vs-model-qp-a1"
    assert remote(["mkdir", "-m", "700", directory]).returncode == 0
    filename = "fixture_model_container.py"
    assert remote(["bash", "-c", 'umask 077; set -C; cat > "$1"', "upload", directory + "/" + filename],
                  Path(__file__).with_name(filename).read_bytes()).returncode == 0
    result = remote(["docker", "exec", container, "python3", "/work/diagnostics/fixture-vs-model-qp-a1/" + filename], timeout=480)
    output = json.loads(result.stdout) if result.stdout else {"experiment_completed": False}
    reference_ssh = ssh[:-1] + ["root@141.61.94.58", shlex.join(["python3", "-"])]
    try:
        reference = subprocess.run(reference_ssh, input=REFERENCE.encode(), capture_output=True, timeout=45)
        output["reference_preflight"] = json.loads(reference.stdout) if reference.returncode == 0 else {"read_only": True, "accessible": False, "ssh_exit": reference.returncode}
    except subprocess.TimeoutExpired:
        output["reference_preflight"] = {"read_only": True, "accessible": False, "timed_out": True}
    print(json.dumps(output))
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
