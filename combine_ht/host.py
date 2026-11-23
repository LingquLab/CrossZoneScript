# Copyright (c) 2026 TileXR Project.
"""Run the pinned SST build and rank batches in this session's Docker container."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

WORK = Path(__file__).resolve().parent
SESSION = WORK.name.removeprefix("tilexr-combine-ht-")
NAME = "tilexr-combine-ht-" + SESSION[:8]
IMAGE = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
REVISIONS = {
    "candidate": "1c9d7fed6188e1093efc456579130de3ca8be4e6",
    "tooling": "23a542b2e6bfcc34f85313d02677a4aafdfc3efb",
}


def call(*argv, timeout=45):
    result = subprocess.run(list(map(str, argv)), capture_output=True, timeout=timeout)
    if result.returncode:
        with (WORK / "host.log").open("ab") as log:
            log.write(result.stdout)
            log.write(result.stderr)
        raise RuntimeError(f"{argv[0]} exit {result.returncode}; see host.log")
    return result.stdout.decode().strip()


def inspect():
    identity = json.loads(call("docker", "inspect", "--format", "{{json .}}", NAME))
    if (identity["Image"] != IMAGE or identity["Config"]["Labels"].get("crosszone.session") != SESSION
            or identity["HostConfig"]["NetworkMode"] != "none"
            or not any(m["Source"] == str(WORK) and m["Destination"] == "/work" for m in identity["Mounts"])):
        raise ValueError("container identity mismatch")
    return identity


def run_sst(args, timeout):
    return json.loads(call("docker", "exec", NAME, "python3", "/work/sst.py",
                           json.dumps(args), timeout=timeout))


def setup():
    if call("docker", "image", "inspect", "--format", "{{.Id}} {{.Os}}/{{.Architecture}}", IMAGE) != IMAGE + " linux/arm64":
        raise ValueError("image identity mismatch")
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
    result = run_sst({"stage": "setup"}, timeout=170)
    result.update(image_id=IMAGE, container_id=identity["Id"], host_work=str(WORK),
                  disk_free_bytes=shutil.disk_usage(WORK).free, revisions=REVISIONS)
    return result


def main(args):
    stage = args["stage"]
    if stage == "setup":
        return setup()
    identity = inspect()
    running = identity["State"]["Running"]
    processes = call("docker", "top", NAME, "-eo", "pid,comm,stat").splitlines()[1:] if running else []
    active = any(row.split()[1] in ("sst", "sstsim.x") and not row.split()[2].startswith("Z") for row in processes)
    if stage == "status":
        progress = WORK / "progress.json"
        return dict(ok=True, running=running, sst_active=active,
                    progress=json.loads(progress.read_text()) if progress.exists() else None)
    if active:
        raise RuntimeError("SST already active in the task container")
    if stage == "stop":
        if running:
            call("docker", "stop", NAME)
        return dict(ok=not inspect()["State"]["Running"], host_work=str(WORK))
    if stage != "run" or not running:
        raise ValueError("a running task container is required")
    if json.loads((WORK / "revisions.json").read_text()) != REVISIONS:
        raise ValueError("staged revisions differ from the runner")
    batch = args["rank_batch"]
    if not batch or len(batch) > 2 or batch != sorted(set(batch)) or set(batch) - {8, 16, 32, 64}:
        raise ValueError("expected one or two ordered rank counts")
    results = []
    for ranks in batch:
        case = run_sst({**args, "ranks": ranks}, 2 * args["stage_timeout"] + 110)
        results.append(case)
        (WORK / f'batch-bs{args["tokens"]}-{batch[0]}p.json').write_text(json.dumps(results, indent=2))
        if not case["ok"]:
            break
    return dict(ok=len(results) == len(batch) and all(case["ok"] for case in results), cases=results)


if __name__ == "__main__":
    try:
        result = main(json.loads(sys.argv[1]))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        result = dict(ok=False, error=type(error).__name__, detail=str(error)[:300], report=str(WORK))
    print(json.dumps(result, ensure_ascii=True))
