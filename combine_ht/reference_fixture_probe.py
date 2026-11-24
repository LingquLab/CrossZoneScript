"""Transfer fixed public reproduction assets and compare installed V1.1 model profiles."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    assert args in ({}, {"status": True}, {"resume_default": True}, {"default_status": True})
    key = Path(os.environ["USERPROFILE"]) / ".ssh/id_ed25519"
    ssh = ["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
           "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12", "root@141.61.94.26"]

    def remote(argv, payload=None, timeout=30):
        return subprocess.run(ssh + [shlex.join(argv)], input=payload, capture_output=True, timeout=timeout)

    name = "tilexr-combine-ht-f71d0252-v11"
    result = remote(["docker", "inspect", name])
    assert result.returncode == 0
    identity = json.loads(result.stdout)[0]
    assert identity["Id"] == "0686f95f26f3e703650724ee7e6da7293b11f7e4ce7154d80e6a089a6614d4c9"
    assert identity["Image"] == "sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f"
    assert identity["HostConfig"]["NetworkMode"] == "none"
    assert identity["Config"]["Labels"]["crosszone.session"] == "f71d0252-ef1e-4b5c-b299-c2429ebcfbdf"
    result = remote(["docker", "top", name, "-eo", "pid,comm"])
    assert result.returncode == 0 and not any(line.split()[-1] in ("sst", "sstsim.x") for line in result.stdout.decode().splitlines()[1:])
    work = "/var/tmp/tilexr-combine-ht-f71d0252-ef1e-4b5c-b299-c2429ebcfbdf-v11"
    directory = work + "/diagnostics/reference-fixture-model-a1"
    if args.get("status") or args.get("default_status"):
        result = remote(["cat", directory + ("/default-summary.json" if args.get("default_status") else "/summary.json")])
        assert result.returncode == 0
        print(json.dumps(json.loads(result.stdout)))
        return 0
    result = remote(["sha256sum", work + "/sst.py"])
    assert result.returncode == 0 and result.stdout.decode().split()[0] == hashlib.sha256(Path(__file__).with_name("sst.py").read_bytes()).hexdigest()
    if args.get("resume_default"):
        filename = "reference_fixture_default_container.py"
        result = remote(["bash", "-c", 'umask 077; set -C; cat > "$1"', "upload", directory + "/" + filename],
                        Path(__file__).with_name("reference_fixture_container.py").read_bytes())
        assert result.returncode == 0
        result = remote(["docker", "exec", name, "python3", "/work/diagnostics/reference-fixture-model-a1/" + filename,
                         "resume-default"], timeout=500)
        if result.stdout:
            print(json.dumps(json.loads(result.stdout)))
        return 0 if result.returncode == 0 else 1
    assert remote(["mkdir", "-m", "700", directory]).returncode == 0
    assert remote(["mkdir", "-m", "700", directory + "/assets"]).returncode == 0
    files = {"reference_fixture_container.py": Path(__file__).with_name("reference_fixture_container.py")}
    assets = Path(__file__).with_name("reference_assets")
    files.update({"assets/" + path.name: path for path in assets.iterdir() if path.is_file()})
    assert len(files) == 5
    for filename, path in files.items():
        result = remote(["bash", "-c", 'umask 077; set -C; cat > "$1"', "upload", directory + "/" + filename], path.read_bytes(), timeout=120)
        assert result.returncode == 0, "asset_upload_failed"
    result = remote(["docker", "exec", name, "python3", "/work/diagnostics/reference-fixture-model-a1/reference_fixture_container.py"], timeout=900)
    if result.stdout:
        print(json.dumps(json.loads(result.stdout)))
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
