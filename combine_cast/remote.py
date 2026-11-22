"""Pin a CPU-only model and retain simulator output for the digest-checked case."""
import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile

root = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54")
case = root / "combine-memory-cast-c266-b1"
reference = root / "lr8-source/lr8-8p-512b"
platform = root / "inter-server-native/t4-sst-mte-drain-count-cache-38d743f7"
name = "tilexr-cpu-b4f4660a-combine-cast"
container_case = "/work/" + case.name


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def docker(*args, timeout=30, **kwargs):
    return subprocess.run(["docker", *args], timeout=timeout, **kwargs)


def main(result):
    if not (reference / "v310_config").is_dir() or not (platform / "installed_model.py").is_file():
        result.update(status="platform_missing", reference_exists=reference.is_dir(), platform_exists=platform.is_dir())
        return
    package = base64.b64decode(PACKAGE, validate=True)
    assert hashlib.sha256(package).hexdigest() == IDENTITY["archive_sha256"]
    case.mkdir(exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as stream:
        members = stream.getmembers()
        assert len(members) < 128 and sum(m.size for m in members) < 40 * 1024 * 1024
        for member in members:
            rel = PurePosixPath(member.name)
            assert member.isfile() and not rel.is_absolute() and ".." not in rel.parts
            target = case / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as out:
                shutil.copyfileobj(stream.extractfile(member), out)
    assert all(sha(case / f) == digest for f, digest in IDENTITY["objects"].items())
    for directory in ("v310_config", "UBusSim_config"):
        shutil.copytree(reference / directory, case / directory)
    for file in ("test_UBusSim.py", "installed_model.py", "environment.snapshot.json"):
        shutil.copyfile(platform / file, case / file)
    shutil.copyfile(case / "workload.yml", case / "v310_config/workload_config.yml")
    info = docker("create", "--name", name, "--runtime=runc", "--network=none", "--cap-drop=ALL",
                  "--security-opt=no-new-privileges", "--cpus=8", "--memory=12g", "--pids-limit=1024",
                  "--shm-size=64m", "--mount", f"type=bind,src={root},dst=/work",
                  "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5",
                  "/bin/bash", "-lc", "exec sleep infinity", capture_output=True, check=True)
    identity = info.stdout.decode().strip()
    try:
        docker("start", identity, capture_output=True, check=True)
        cpu = "import glob; assert not any(glob.glob(p) for p in ('/dev/davinci*','/dev/devmm*','/dev/hisi_hdc*'))"
        docker("exec", identity, "python3", "-c", cpu, capture_output=True, check=True)
        result["cpu_only"] = True
        result["status"] = "sst_running"
        (case / "result.json").write_text(json.dumps(result))
        command = 'cd "$1"; source /opt/sim/sst-env.sh; exec /opt/sim/local/sstcore/bin/sst -n 8 ./test_UBusSim.py'
        with (case / "sim.log").open("wb") as log:
            run = docker("exec", identity, "bash", "-c", command, "bash", container_case,
                         stdout=log, stderr=subprocess.STDOUT, timeout=780)
        result["sst_exit"] = run.returncode
        tail = (case / "sim.log").read_bytes()[-65536:]
        complete = re.search(rb"Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)", tail)
        result["simulated_time"] = [part.decode("ascii") for part in complete.groups()] if complete else None
        result["dump_files"] = len(list((case / "bin").glob("*.bin")))
        if run.returncode == 0:
            check = docker("exec", identity, "python3", container_case + "/check.py", container_case,
                           "/opt/sim/plugins/libDavinciSimulator.so", capture_output=True, timeout=100)
            (case / "check.log").write_bytes(check.stdout + check.stderr)
            result["checker_exit"] = check.returncode
            if check.returncode == 0:
                result["cases"] = json.loads(check.stdout)
                rows = [entry for row in result["cases"] for entry in row.values() if isinstance(entry, dict)]
                result["status"] = ("verified" if rows and all(row.get("mismatches") == 0 and row.get("guards_ok")
                                                               for row in rows) else "output_mismatch")
            else:
                result["status"] = "checker_error"
        else:
            result["status"] = "sst_failed"
            result["log_categories"] = [word for word in ("FileNotFoundError", "AssertionError", "KeyError",
                                                          "ValueError", "Fatal", "illegal") if word.encode() in tail]
    finally:
        stopped = docker("rm", "-f", identity, capture_output=True, timeout=35)
        result["container_removed"] = stopped.returncode == 0


result = {"status": "preparing", "revision": IDENTITY["revision"], "objects": IDENTITY["objects"]}
try:
    main(result)
except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
    result.update(status="exception", error_type=type(error).__name__)
    if case.is_dir():
        import traceback
        (case / "failure.log").write_text(traceback.format_exc())
if case.is_dir():
    (case / "result.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, separators=(",", ":")))
