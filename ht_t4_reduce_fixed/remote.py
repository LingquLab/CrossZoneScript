"""Run the prebuilt HT final-reduce object in isolated V310 SST."""
import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import time

shared = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54")
case = shared / IDENTITY["case_name"]
mount = f"/work/{case.name}"
reference = shared / "lr8-source/lr8-64p-512b"
platform = shared / "inter-server-native/t4-sst-mte-drain-count-cache-38d743f7"
image = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
name = "tilexr-cpu-b4f4660a-" + IDENTITY["case_name"]
modes = ("ready", "remote-so")


def docker(*args, timeout=20, **kwargs):
    return subprocess.run(["docker", *args], timeout=timeout, **kwargs)


def stage():
    package = base64.b64decode(PACKAGE, validate=True)
    assert hashlib.sha256(package).hexdigest() == IDENTITY["archive_sha256"]
    case.mkdir()
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as archive:
        members = archive.getmembers()
        assert len(members) == 19 and sum(m.size for m in members) < 20 * 1024 * 1024
        for member in members:
            path = PurePosixPath(member.name)
            assert member.isfile() and not path.is_absolute() and ".." not in path.parts
            target = case / path
            target.parent.mkdir(exist_ok=True, parents=True)
            with target.open("xb") as out:
                shutil.copyfileobj(archive.extractfile(member), out)
    assert hashlib.sha256((case / IDENTITY["object"]).read_bytes()).hexdigest() == IDENTITY["object_sha256"]
    for mode in modes:
        folder = case / mode
        for directory in ("v310_config", "UBusSim_config"):
            shutil.copytree(reference / directory, folder / directory)
        for file in ("test_UBusSim.py", "installed_model.py", "environment.snapshot.json"):
            shutil.copyfile(platform / file, folder / file)
        shutil.copyfile(folder / "workload.yml", folder / "v310_config/workload_config.yml")


def run_point(identity, mode):
    folder = case / mode
    row = json.loads((folder / "manifest.json").read_text())
    row.pop("exports")
    started = time.monotonic()
    command = 'cd "$1"; source /opt/sim/sst-env.sh; exec /opt/sim/local/sstcore/bin/sst -n 1 ./test_UBusSim.py'
    with (folder / "sim.log").open("wb") as log:
        try:
            run = docker("exec", identity, "bash", "-c", command, "bash", f"{mount}/{mode}",
                         stdout=log, stderr=subprocess.STDOUT, timeout=105)
        except subprocess.TimeoutExpired:
            row.update(executed_sst=True, timeout=True, wall_seconds=round(time.monotonic()-started, 3))
            return row
    row.update(executed_sst=True, sst_exit=run.returncode, wall_seconds=round(time.monotonic()-started, 3),
               dump_files=len(list((folder / "bin").glob("*.bin"))))
    tail = (folder / "sim.log").read_bytes()[-16384:]
    match = re.search(rb"Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)", tail)
    if match:
        row["simulated_time"] = [part.decode() for part in match.groups()]
    if run.returncode:
        row["sim_tail"] = [re.sub(r"0x[0-9a-f]+", "<address>", s)[:140]
                           for s in tail.decode(errors="replace").splitlines()[-4:]]
        return row
    check = docker("exec", identity, "python3", f"{mount}/check.py", f"{mount}/{mode}",
                   capture_output=True, timeout=30)
    (folder / "check.log").write_bytes(check.stdout + check.stderr)
    row["checker_exit"] = check.returncode
    if check.returncode == 0:
        row.update(json.loads(check.stdout))
    else:
        row["checker_error"] = check.stderr.decode(errors="replace").splitlines()[-1][:170] if check.stderr else ""
    return row


def main(result):
    diagnostic = shared / "ht-t4-reduce-scalar-ready-a1999862" / "result.json"
    deadline = time.monotonic() + 55
    while time.monotonic() < deadline:
        if diagnostic.is_file():
            try:
                previous = json.loads(diagnostic.read_text())
            except ValueError:
                previous = {}
            if previous.get("container_removed") is True:
                probe = previous.get("cases", {}).get("ready", {})
                result["scalar_diagnostic"] = {key: probe.get(key) for key in
                    ("status", "mismatches", "simulated_time", "first")}
                break
        time.sleep(3)
    else:
        result["blocked"] = "scalar_diagnostic_terminal_receipt_missing"
        return
    stage()
    result["archive_and_object_verified"] = True
    create = docker("create", "--name", name, "--runtime=runc", "--network=none", "--cap-drop=ALL",
                    "--security-opt=no-new-privileges", "--cpus=2", "--memory=12g", "--pids-limit=1024",
                    "--mount", f"type=bind,src={case},dst={mount}", image,
                    "/bin/bash", "-lc", "exec sleep infinity", capture_output=True, check=True)
    identity = create.stdout.decode().strip()
    try:
        docker("start", identity, capture_output=True, check=True)
        setup = """import glob,sys,yaml
from pathlib import Path
assert not any(glob.glob(p) for p in ('/dev/davinci*','/dev/devmm*','/dev/hisi_hdc*'))
for folder in Path(sys.argv[1]).iterdir():
 if not folder.is_dir() or not (folder/'manifest.json').is_file():continue
 for rel in ('v310_config/davidV100_simulator_config.yml','UBusSim_config/UBusSim_simulator_config.yml'):
  file=folder/rel
  config=yaml.safe_load(file.read_text())
  if rel.startswith('v310'):
   config['architecture_config']['chip_num']=1
   config['simulator_config']['net_sim']['params']['chip_num']=1
  else:
   assert config['simulator_info']['V310_flag'] is True
   config['simulator_info']['UBusSim_flag']=False
  file.write_text(yaml.safe_dump(config,sort_keys=False))
"""
        prep = docker("exec", identity, "python3", "-c", setup, mount, capture_output=True, timeout=30)
        (case / "setup.log").write_bytes(prep.stdout+prep.stderr)
        if prep.returncode:
            result["setup_exit"] = prep.returncode
            result["setup_error"] = prep.stderr.decode(errors="replace").splitlines()[-1][:170]
            return
        result["cpu_only"] = True
        for mode in modes:
            row = run_point(identity, mode)
            result["cases"][mode] = row
            (case / "result.json").write_text(json.dumps(result, indent=2))
            if row.get("timeout"):
                break
            # The stale-marker cases need a control that reached reduction.
            if mode == "ready" and not (row.get("status_ok") and row.get("mismatches") == 0 and all(row.get("guards", {}).values())):
                break
    finally:
        result["container_removed"] = docker("rm", "-f", identity, capture_output=True, timeout=25).returncode == 0


result = dict(IDENTITY, cases={})
try:
    main(result)
except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
    result["error"] = type(error).__name__
    if case.is_dir():
        import traceback
        (case / "failure.log").write_text(traceback.format_exc())
if case.is_dir():
    (case / "result.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, separators=(",", ":")))
