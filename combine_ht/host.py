# Copyright (c) 2026 TileXR Project.
"""Validate container identity and run only this session's SST phases."""

import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys

SESSION = "500e2a93-b84e-4d36-a9aa-8d2cd0321cdf"
WORK = Path(__file__).resolve().parent
NAME = "tilexr-combine-ht-" + SESSION[:8] + "-v11"
IMAGE = "sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f"
ARCHIVE = Path("/mnt/share/simulator/images/tilexr-sim-9.1-aarch64-v1.1.0.tar.gz")
REVISIONS = {"candidate": "1c9d7fed6188e1093efc456579130de3ca8be4e6",
             "tooling": "23a542b2e6bfcc34f85313d02677a4aafdfc3efb"}


def call(*argv, timeout=45):
    p = subprocess.run(list(map(str, argv)), capture_output=True, timeout=timeout)
    with (WORK / "host.log").open("ab") as log:
        log.write(p.stdout + p.stderr)
    if p.returncode:
        raise RuntimeError("command_exit_" + str(p.returncode))
    return p.stdout.decode().strip()


def inspect():
    info = json.loads(call("docker", "inspect", "--format", "{{json .}}", NAME))
    assert info["Image"] == IMAGE
    assert info["Config"]["Labels"].get("crosszone.session") == SESSION
    assert info["HostConfig"]["NetworkMode"] == "none"
    assert any(m["Source"] == str(WORK) and m["Destination"] == "/work" for m in info["Mounts"])
    return info


def run_sst(args, timeout):
    result = json.loads(call("docker", "exec", NAME, "python3", "/work/sst.py", json.dumps(args), timeout=timeout))
    assert isinstance(result.get("ok"), bool)
    return result


def setup():
    assert not (WORK / "environment.snapshot.json").exists(), "setup_already_attempted"
    query = ["docker", "image", "inspect", "--format", "{{.Id}} {{.Os}}/{{.Architecture}}", IMAGE]
    if subprocess.run(query, capture_output=True, timeout=30).returncode:
        assert ARCHIVE.stat().st_size == 4107108475
        call("docker", "load", "-i", ARCHIVE, timeout=600)
    assert call(*query) == IMAGE + " linux/arm64"
    for name in REVISIONS:
        folder = WORK / name
        folder.mkdir(exist_ok=True)
        call("tar", "-xf", WORK / (name + ".tar"), "-C", folder)
    (WORK / "revisions.json").write_text(json.dumps(REVISIONS) + "\n")
    exists = subprocess.run(["docker", "container", "inspect", NAME], capture_output=True, timeout=20)
    if exists.returncode:
        call("docker", "run", "-dt", "--name", NAME, "--label", "crosszone.session=" + SESSION,
             "--network=none", "--runtime=ascend", "--ipc=shareable", "--shm-size=64m",
             "--ulimit", "nofile=524288:524288", "--mount", "type=bind,src=" + str(WORK) + ",dst=/work",
             IMAGE, "/bin/bash", "-l", timeout=75)
    identity = inspect()
    if not identity["State"]["Running"]:
        call("docker", "start", NAME)
    result = run_sst({"stage": "setup"}, timeout=240)
    result.update(image_id=IMAGE, container_id=identity["Id"], host_work=str(WORK),
                  disk_free_bytes=shutil.disk_usage(WORK).free, revisions=REVISIONS)
    return result


def main(args):
    assert socket.gethostname().split(".")[0] == args["hostname"] == "host-94-26"
    assert WORK.name == "tilexr-combine-ht-" + SESSION + "-v11"
    stage = args["stage"]
    if stage == "setup":
        return setup()
    info = inspect()
    assert args["container_id"] == info["Id"]
    running = info["State"]["Running"]
    rows = call("docker", "top", NAME, "-eo", "pid,comm,stat").splitlines()[1:] if running else []
    active = any(row.split()[1] in ("sst", "sstsim.x") and not row.split()[2].startswith("Z") for row in rows)
    if stage == "status":
        progress = WORK / "progress.json"
        return dict(ok=True, running=running, sst_active=active,
                    progress=json.loads(progress.read_text()) if progress.exists() else None)
    assert not active, "session_sst_already_active"
    if stage == "stop":
        if running:
            call("docker", "stop", NAME)
        return dict(ok=not inspect()["State"]["Running"], host_work=str(WORK))
    assert running
    assert json.loads((WORK / "revisions.json").read_text()) == REVISIONS
    if stage == "refresh":
        return dict(ok=True, host_work=str(WORK))
    if stage in ("run", "inspect"):
        return run_sst(args, args.get("stage_timeout", 60) + 95)
    raise ValueError("unsupported_stage")


if __name__ == "__main__":
    try:
        result = main(json.loads(sys.argv[1]))
    except (OSError, ValueError, RuntimeError, AssertionError, subprocess.SubprocessError) as error:
        result = dict(ok=False, error_type=type(error).__name__, detail=str(error)[:300], host_work=str(WORK))
    print(json.dumps(result, ensure_ascii=True))
