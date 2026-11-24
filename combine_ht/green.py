# Copyright (c) 2026 TileXR Project.
"""Fetch pinned public inputs and invoke the session's isolated SST host runner."""

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile

SESSION = "f71d0252-ef1e-4b5c-b299-c2429ebcfbdf"
WORK = "/var/tmp/tilexr-combine-ht-" + SESSION + "-v11"
ORIGIN = "https://github.com/LingquLab/TileXR.git"
INPUTS = (
    ("candidate", "54b1af8e439f9300abe70aed3d51f578cdbb1f3b",
     ("include", "src/moonep/combine_ht", "tests/moonep_combine_ht/combine_ht.mapping.template.json")),
    ("tooling", "23a542b2e6bfcc34f85313d02677a4aafdfc3efb", ("tools/unitest/simulator",)),
)


def main(args):
    stage = args["stage"]
    assert stage in ("setup", "refresh", "run", "status", "stop", "inspect", "read")
    assert args["ssh_target"] == "root@141.61.94.26"
    assert args["hostname"] == "host-94-26"
    root = Path(os.environ["LOCALAPPDATA"]) / "CrossZone" / "tilexr" / (SESSION + "-v11")
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / (stage + ".log")
    key = Path(os.environ["USERPROFILE"]) / ".ssh" / "id_ed25519"
    ssh = ["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
           "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12", args["ssh_target"]]

    def call(argv, timeout, payload=None):
        p = subprocess.run(list(map(str, argv)), input=payload, capture_output=True, timeout=timeout)
        with log_path.open("ab") as log:
            log.write(p.stderr)
            if p.returncode:
                log.write(p.stdout)
        if p.returncode:
            raise RuntimeError("subprocess_exit_" + str(p.returncode))
        return p.stdout

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
                ref = "refs/heads/" + name
                call(["git", "-C", repo, "fetch", "--no-tags", "--depth=1", ORIGIN, revision + ":" + ref], 240)
                assert call(["git", "-C", repo, "rev-parse", ref], 15).decode().strip() == revision
                archive = root / (name + ".tar")
                call(["git", "-C", repo, "archive", "--format=tar", "-o", archive, revision, *paths], 45)
                upload(archive)
        if stage in ("run", "inspect", "read"):
            status_args = {**args, "stage": "status"}
            status = json.loads(remote(["python3", WORK + "/host.py", json.dumps(status_args)], 60))
            assert status["ok"] and status["running"] and not status["sst_active"], "session_sst_not_idle"
        if stage in ("setup", "refresh", "run", "inspect", "read"):
            scripts = root / "scripts.tar"
            with tarfile.open(scripts, "w") as package:
                for path in sorted(Path(__file__).parent.glob("*.py")):
                    package.add(path, arcname=path.name)
                package.add(Path(__file__).with_name("prefill_bridge.cpp"), arcname="prefill_bridge.cpp")
            upload(scripts)
            remote(["tar", "-xf", WORK + "/scripts.tar", "-C", WORK], 20)
        timeout = 960 if stage == "setup" else (args.get("stage_timeout", 60) + 120)
        result = json.loads(remote(["timeout", "--kill-after=25", str(timeout), "python3",
                                    WORK + "/host.py", json.dumps(args)], timeout + 45))
    except (OSError, ValueError, RuntimeError, AssertionError, subprocess.SubprocessError) as error:
        result = dict(ok=False, stage=stage, error_type=type(error).__name__,
                      detail=str(error) if isinstance(error, RuntimeError) else "see_green_local_log",
                      green_log=str(log_path))
    (root / (stage + ".json")).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    sys.exit(main(json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))))
