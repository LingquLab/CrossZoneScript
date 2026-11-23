# Copyright (c) 2026 TileXR Project.
"""Stage exact Git revisions via Windows and invoke the assigned Linux SST host."""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile

SESSION = "c83978c3-9516-4c83-ac3a-d23a05725bf3"
WORK = "/var/tmp/tilexr-combine-ht-" + SESSION + "-v11"
ORIGIN = "https://github.com/LingquLab/TileXR.git"
INPUTS = (
    ("candidate", "1c9d7fed6188e1093efc456579130de3ca8be4e6", ("include", "src/moonep/combine_ht")),
    ("tooling", "23a542b2e6bfcc34f85313d02677a4aafdfc3efb", ("tools/unitest/simulator",)),
)


def main(args):
    stage = args["stage"]
    if stage not in ("setup", "refresh", "status", "run", "batch-report", "send-fault", "stop"):
        raise ValueError("unknown stage")
    if not re.fullmatch(r"root@[0-9.]+", args["ssh_target"]) or not re.fullmatch(r"host-[0-9-]+", args["hostname"]):
        raise ValueError("invalid simulator target")
    root = Path(os.environ["LOCALAPPDATA"]) / "CrossZone" / "tilexr" / (SESSION + "-v11")
    root.mkdir(parents=True, exist_ok=True)
    receipt = root / f"{stage}.json"
    log_path = root / f"{stage}.log"
    key = Path(os.environ["USERPROFILE"]) / ".ssh" / "id_ed25519"
    ssh = ["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes",
           "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=12", args["ssh_target"]]

    def call(argv, timeout, payload=None):
        result = subprocess.run(list(map(str, argv)), input=payload, capture_output=True, timeout=timeout)
        with log_path.open("ab") as log:
            log.write(result.stderr)
            if result.returncode:
                log.write(result.stdout)
                raise RuntimeError(f"{argv[0]} exit {result.returncode}")
        return result.stdout

    def remote(argv, timeout, payload=None):
        return call(ssh + [shlex.join(map(str, argv))], timeout, payload)

    def upload(path):
        remote(["timeout", "120", "bash", "-c", 'set -e; umask 077; cat > "$1.tmp"; mv "$1.tmp" "$1"',
                "upload", WORK + "/" + path.name], 140, path.read_bytes())

    try:
        if stage == "setup":
            remote(["bash", "-c", 'set -e; test "$(hostname -s)" = "$1"; test "$(uname -m)" = aarch64; umask 077; mkdir -p "$2"',
                    "preflight", args["hostname"], WORK], 30)
            repo = root / "TileXR.git"
            if not repo.exists():
                call(["git", "init", "--bare", repo], 20)
            for name, revision, paths in INPUTS:
                reference = "refs/heads/" + name
                call(["git", "-C", repo, "fetch", "--no-tags", "--depth=1", ORIGIN, revision + ":" + reference], 240)
                if call(["git", "-C", repo, "rev-parse", reference], 15).decode().strip() != revision:
                    raise ValueError("Git revision mismatch")
                archive = root / (name + ".tar")
                call(["git", "-C", repo, "archive", "--format=tar", "-o", archive, revision, *paths], 45)
                upload(archive)
        if stage in ("setup", "refresh"):
            scripts = root / "scripts.tar"
            with tarfile.open(scripts, "w") as package:
                for name in ("host.py", "sst.py", "fixtures.py"):
                    package.add(Path(__file__).with_name(name), arcname=name)
            upload(scripts)
            remote(["tar", "-xf", WORK + "/scripts.tar", "-C", WORK], 20)

        if stage == "send-fault":
            container = "tilexr-combine-ht-" + SESSION[:8] + "-v11"
            identity = remote(["docker", "inspect", "--format", "{{.Id}} {{.State.Running}}", container], 30)
            if identity.decode().strip() != args["container_id"] + " true":
                raise ValueError("diagnostic container identity mismatch")
            procs = remote(["docker", "top", container, "-eo", "pid,comm,stat"], 30).decode().splitlines()[1:]
            if any(line.split()[1] in ("sst", "sstsim.x") and not line.split()[2].startswith("Z") for line in procs):
                raise RuntimeError("SST already active in the diagnostic container")
            for name in ("send_fault.py", "sst_fault.c"):
                upload(Path(__file__).with_name(name))
            output = remote(["timeout", "--kill-after=25", "510", "docker", "exec", container,
                             "python3", "/work/send_fault.py"], 565)
            result = json.loads(output)
            receipt.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result, ensure_ascii=True))
            return 0

        timeout = 930 if stage == "setup" else 90
        if stage == "run":
            timeout = len(args["rank_batch"]) * (2 * args["stage_timeout"] + 120) + 20
        result = json.loads(remote(["timeout", "--kill-after=25", str(timeout), "python3",
                                    WORK + "/host.py", json.dumps(args)], timeout + 45))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        detail = str(error) if type(error) is RuntimeError else type(error).__name__
        result = dict(ok=False, stage=stage, detail=detail, log=str(log_path))
    receipt.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    sys.exit(main(json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))))
