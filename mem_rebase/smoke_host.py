"""Run the qualified MEM fixture using a fresh container on server 30."""
import json
from pathlib import Path
import subprocess
import tempfile

image = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
seed = "tilexr_alltoall_compare_20261113"
seed_report = "/opt/sim/alltoall-group-schedule-347747af-20261118/environment-report.json"
work = Path(tempfile.mkdtemp(prefix="tilexr-final-memory-", dir="/opt"))
revision = args["revision"]
root = "/work/result"

def docker(*arguments, timeout=45, data=None):
    return subprocess.run(["docker", *arguments], input=data, capture_output=True,
                          check=True, timeout=timeout).stdout

name = "tilexr-final-mem-" + work.name.rsplit("-", 1)[1].lower()
assert docker("image", "inspect", "--format", "{{.Id}}", image).decode().strip() == image
assert docker("inspect", "--format", "{{.Image}}", seed).decode().strip() == image
docker("cp", seed + ":" + seed_report, str(work / "environment-report.json"))
docker("run", "-dt", "--name", name, "--network", "none", "--runtime", "ascend",
       "--ipc", "shareable", "--shm-size", "4g", "-v", str(work) + ":/work", image,
       "/bin/bash", "-l")
print(json.dumps(dict(stage="started", revision=revision, work=str(work))), flush=True)
try:
    environment = json.loads((work / "environment-report.json").read_text())
    for path in environment["files"]:
        path = Path(path)
        assert path.is_absolute() and ".." not in path.parts
        present = subprocess.run(["docker", "exec", name, "test", "-f", str(path)],
                                 capture_output=True, timeout=15)
        if present.returncode == 0:
            continue
        docker("exec", name, "mkdir", "-p", str(path.parent))
        with tempfile.TemporaryDirectory(dir=work) as staging:
            target = Path(staging) / path.name
            docker("cp", "-L", seed + ":" + str(path), str(target))
            docker("cp", str(target), name + ":" + str(path))
    stages = {
        "build": (build_source.replace("ARGS_VALUE", repr(args)).replace("ROOT_VALUE", repr(root))
                  .replace("ARCHIVE_VALUE", repr(archive)), 1550),
        "run": (run_source.replace("ARGS_VALUE", repr(args)).replace("ROOT_VALUE", repr(root))
                .replace("RUNNERS_VALUE", repr(runners)), 5700),
    }
    for stage, (source, timeout) in stages.items():
        with (work / (stage + ".stderr.log")).open("wb") as errors:
            result = subprocess.run(["docker", "exec", "-i", name, "python3", "-u", "-"],
                                    input=source.encode(), stdout=subprocess.PIPE,
                                    stderr=errors, timeout=timeout)
        (work / (stage + ".stdout.log")).write_bytes(result.stdout)
        for line in result.stdout.splitlines():
            print(json.dumps(json.loads(line)), flush=True)
        if result.returncode:
            print(json.dumps(dict(stage=stage, exit=result.returncode)), flush=True)
            raise SystemExit(result.returncode)
finally:
    docker("rm", "-f", name, timeout=35)
