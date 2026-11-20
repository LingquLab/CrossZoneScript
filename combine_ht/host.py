# Copyright (c) 2026 TileXR Project.
"""Manage the session-owned container and retain full Linux-side execution logs."""

import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

WORK = Path(__file__).resolve().parent
NAME = "tilexr-combine-ht-c1d52c19"
SESSION = "c1d52c19-f831-4174-b368-933ccbabec24"
IMAGE = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
REVISIONS = {
    "candidate": "233c0c307d08e12c5ed95e76aad08910f5aebc6c",
    "tooling": "23a542b2e6bfcc34f85313d02677a4aafdfc3efb",
}


def command(*argv, timeout=50):
    result = subprocess.run(list(map(str, argv)), capture_output=True, timeout=timeout)
    if result.returncode:
        with (WORK / "host.log").open("ab") as log:
            log.write(result.stdout)
            log.write(result.stderr)
        raise RuntimeError("command exited " + str(result.returncode) + " (" + str(argv[0]) + ")")
    return result.stdout.decode().strip()


def container():
    identity = json.loads(command("docker", "inspect", "--format",
                                  "{{json .}}", NAME))
    if (identity["Image"] != IMAGE or
            identity["Config"]["Labels"].get("crosszone.session") != SESSION or
            identity["HostConfig"]["NetworkMode"] != "none" or
            not any(m["Source"] == str(WORK) and m["Destination"] == "/work" for m in identity["Mounts"])):
        raise ValueError("container identity mismatch")
    return identity


def setup():
    if platform.machine() != "aarch64" or platform.node().split(".")[0] != "host-94-26":
        raise ValueError("simulator host identity mismatch")
    if command("docker", "image", "inspect", "--format", "{{.Id}} {{.Os}}/{{.Architecture}}", IMAGE) != IMAGE + " linux/arm64":
        raise ValueError("simulator image identity mismatch")
    available = shutil.disk_usage(WORK).free
    if available < 3 * 1024**3:
        raise ValueError("insufficient free space for the SST smoke case")
    for name in REVISIONS:
        (WORK / name).mkdir(exist_ok=True)
        command("tar", "-xf", WORK / (name + ".tar"), "-C", WORK / name)
    (WORK / "revisions.json").write_text(json.dumps(REVISIONS))
    container_exists = subprocess.run(["docker", "inspect", NAME], capture_output=True, timeout=20).returncode == 0
    if not container_exists:
        command("docker", "run", "-dt", "--name", NAME, "--label", "crosszone.session=" + SESSION,
                "--network=none", "--runtime=ascend", "--ipc=shareable", "--shm-size=64m",
                "--ulimit", "nofile=524288:524288", "--mount", "type=bind,src=" + str(WORK) + ",dst=/work",
                IMAGE, "/bin/bash", "-l", timeout=75)
    identity = container()
    if not identity["State"]["Running"]:
        command("docker", "start", NAME)
    result = json.loads(command("docker", "exec", NAME, "python3", "/work/sst.py",
                                '{"stage":"setup"}', timeout=160))
    result.update(image_id=IMAGE, container_id=identity["Id"], host_work=str(WORK),
                  disk_free_bytes=available, revisions=REVISIONS)
    return result


def main(args):
    stage = args["stage"]
    try:
        if stage == "setup":
            result = setup()
        else:
            identity = container()
            if stage == "run":
                result = json.loads(command("docker", "exec", NAME, "python3", "/work/sst.py",
                                            json.dumps(args), timeout=2 * args["stage_timeout"] + 120))
            elif stage == "status":
                result = {"ok": True, "running": identity["State"]["Running"],
                          "progress": json.loads((WORK / "progress.json").read_text())
                                      if (WORK / "progress.json").is_file() else None}
            elif stage == "stop":
                if identity["State"]["Running"]:
                    processes = command("docker", "top", NAME, "-eo", "comm").splitlines()
                    if any(name.strip() in ("sst", "sstsim.x") for name in processes):
                        raise RuntimeError("SST process is still running")
                    command("docker", "stop", NAME)
                result = {"ok": not container()["State"]["Running"], "host_work": str(WORK)}
            else:
                raise ValueError("unknown stage")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        result = {"ok": False, "stage": stage, "error": type(exc).__name__,
                  "detail": str(exc) if type(exc) is RuntimeError else "", "log": str(WORK / "host.log")}
        with (WORK / "host.log").open("a") as log:
            log.write(str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
