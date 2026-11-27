"""Replace the V310 libraries inside a derived image and execute the original Cast suite."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback

ROOT = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54")
PATCH = ROOT / "cast-v310-repair-0784b139"
BASE = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
TAG = "tilexr-v310-sst:cast-fix-0784b139"
REFERENCE = ROOT / "lr8-source/lr8-64p-512b"
PLATFORM = ROOT / "inter-server-native/t4-sst-mte-drain-count-cache-38d743f7"
LIB_DIR = PATCH / "lib"
SUITE = PATCH / OPTIONS.get("run_name", "regression")
assert SUITE.parent == PATCH and re.fullmatch(r"regression(?:-[a-z0-9]+)?", SUITE.name)
SHELL = (
    'set -e; cd "$1"; source /opt/sim/sst-env.sh; '
    'LD_DEBUG=libs exec /opt/sim/local/sstcore/bin/sst -n 1 ./test_UBusSim.py'
)
CONFIGURE = """
from pathlib import Path
import sys,yaml
folder = Path(sys.argv[1])
v310 = folder/'v310_config/davidV100_simulator_config.yml'
ubus = folder/'UBusSim_config/UBusSim_simulator_config.yml'
d = yaml.safe_load(v310.read_text())
d['architecture_config']['chip_num'] = 1
d['simulator_config']['net_sim']['params']['chip_num'] = 1
v310.write_text(yaml.safe_dump(d,sort_keys=False))
d = yaml.safe_load(ubus.read_text())
assert d['simulator_info']['V310_flag'] is True
d['simulator_info']['UBusSim_flag'] = False
ubus.write_text(yaml.safe_dump(d,sort_keys=False))
"""
CHECK = """
import json,sys
from pathlib import Path
import check
if sys.argv[3] == 'bf16-writeback':
    print(json.dumps(check.check(Path(sys.argv[1]))))
else:
    check.V310_DECODER_SHA256 = sys.argv[2]
    print(json.dumps(check.check(Path(sys.argv[1]), Path('/opt/sim/plugins/libDavinciSimulator.so'))))
"""


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def docker(*args, timeout=45, **kwargs):
    return subprocess.run(["docker", *args], timeout=timeout, **kwargs)


def capture(*args, timeout=45):
    return docker(*args, capture_output=True, check=True, timeout=timeout).stdout


def write(path, record):
    path.write_text(json.dumps(record, indent=2) + "\n")


def extract_inputs():
    package = SUITE / "inputs"
    seen = set()
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz") as archive:
        for entry in archive:
            assert entry.isfile() and entry.name in INPUTS and entry.name not in seen
            path = package / entry.name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                shutil.copyfileobj(archive.extractfile(entry), stream)
            assert digest(path) == INPUTS[entry.name]
            seen.add(entry.name)
    assert seen == set(INPUTS)
    return package


def derive_image():
    staged = json.loads((PATCH / "staged.json").read_text())
    assert staged["find_exit"] == 0
    files = {}
    for path in sorted(LIB_DIR.glob("*.so")):
        destinations = {real for alias, (real, _) in staged["paths"].items() if Path(alias).name == path.name}
        assert destinations, path.name
        for target in destinations:
            assert target.startswith("/opt/sim/") and Path(target).name == path.name
            files[target] = path
    assert len(set(files.values())) == 7
    container = capture("create", BASE).decode().strip()
    try:
        for target, path in files.items():
            capture("cp", str(path), f"{container}:{target}")
        image = capture("commit", container, TAG, timeout=150).decode().strip()
    finally:
        docker("rm", container, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    probe = "import hashlib,json,sys; print(json.dumps({p:hashlib.sha256(open(p,'rb').read()).hexdigest() for p in sys.argv[1:]}))"
    observed = json.loads(capture("run", "--rm", "--runtime=runc", "--network=none", "--cap-drop=ALL",
                                  "--entrypoint", "python3", image, "-c", probe, *files, timeout=90))
    assert observed == {target: digest(path) for target, path in files.items()}
    write(SUITE / "image.json", {"base": BASE, "image": image, "tag": TAG, "paths": observed})
    return image, observed, {alias: sha for alias, (_, sha) in staged["paths"].items()}


def prepare_case(package, variant, name, spec):
    folder = SUITE / variant / name
    shutil.copytree(package / "cases" / name, folder)
    for obj, sha in spec["objects"].items():
        assert digest(folder / obj) == sha and spec["blocks"] == 1
    work = folder / Path(spec["workload"]).parent
    mount = "/work/" + name
    workload = work / "workload.yml"
    config = workload.read_text()
    assert spec["mount"] in config
    workload.write_text(config.replace(spec["mount"], mount))
    for directory in ("v310_config", "UBusSim_config"):
        shutil.copytree(REFERENCE / directory, work / directory)
    for file in ("test_UBusSim.py", "installed_model.py", "environment.snapshot.json"):
        shutil.copyfile(PLATFORM / file, work / file)
    shutil.copyfile(workload, work / "v310_config/workload_config.yml")
    return folder, work, mount, mount if work == folder else mount + "/ub"


def summarize_check(name, checked):
    if name == "bf16-writeback":
        return {k: [row["checked"], row["errors"], row["first"][:1]] if isinstance(row, dict) else row
                for k, row in checked.items()}
    row, = checked
    return {k: [v["tested"], v["mismatches"], v["bitwise_differences"], v["guards_ok"], v["first"][:1]]
            if "tested" in v else v for k, v in row.items() if isinstance(v, dict)}


def run_case(package, variant, name, spec, image, decoder_sha, libraries):
    row = {}
    folder, work, mount, cwd = prepare_case(package, variant, name, spec)
    container = capture("create", "--runtime=runc", "--network=none", "--cap-drop=ALL",
                        "--security-opt=no-new-privileges", "--cpus=2", "--memory=12g", "--pids-limit=1024",
                        "--mount", f"type=bind,src={folder},dst={mount}", image,
                        "/bin/bash", "-lc", "exec sleep infinity").decode().strip()
    try:
        capture("start", container)
        command = CONFIGURE if name == "bf16-writeback" else None
        setup = ("-c", command, cwd) if command else (mount + "/sync_rank_config.py", cwd)
        prep = docker("exec", container, "python3", *setup, capture_output=True, timeout=30)
        (folder / "setup.log").write_bytes(prep.stdout + prep.stderr)
        if prep.returncode:
            return {"setup_exit": prep.returncode}
        started = time.monotonic()
        with (folder / "sim.log").open("wb") as log:
            ran = docker("exec", container, "bash", "-c", SHELL, "bash", cwd,
                         stdout=log, stderr=subprocess.STDOUT, timeout=200)
        row.update(sst_exit=ran.returncode, sst_wall_seconds=round(time.monotonic() - started, 3))
        raw = (folder / "sim.log").read_bytes()
        row["loaded"] = sorted({Path(p.decode()).name for p in re.findall(rb"calling init: ([^\r\n]+\.so)\s*$", raw, re.M)}
                               & set(libraries))
        done = re.search(rb"Simulation is complete, simulated time: ([0-9.]+) (ns|us|ms|s)", raw)
        row["sim"] = [s.decode() for s in done.groups()] if done else None
        if ran.returncode == 0:
            check = docker("exec", "--workdir", mount, container, "python3", "-c", CHECK, cwd, decoder_sha, name,
                           capture_output=True, timeout=45)
            (folder / "check.log").write_bytes(check.stdout + check.stderr)
            row["check_exit"] = check.returncode
            if check.returncode == 0:
                row["values"] = summarize_check(name, json.loads(check.stdout))
        else:
            row["last_log"] = [line[:170] for line in raw.decode(errors="replace").splitlines()
                               if re.search("undefined symbol|error while loading|Error:|Exception:|AssertionError|Traceback",
                                            line, re.I)][-3:]
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
        row["error"] = type(error).__name__
        (folder / "failure.log").write_text(traceback.format_exc())
    finally:
        docker("rm", "-f", container, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        write(folder / "result.json", row)
    return row


result = {"artifact_dir": SUITE.as_posix()}
try:
    SUITE.mkdir(exist_ok=False)
    package = extract_inputs()
    result["input_files_verified"] = len(INPUTS)
    model, installed, old = derive_image()
    result.update(image=model, tag=TAG, installed_paths=installed)
    cases = json.loads((package / "cases.json").read_bytes())
    labels = sorted({p.name for p in LIB_DIR.glob("*.so")})
    decoder = "/opt/sim/plugins/libDavinciSimulator.so"
    patched_decoder = next(sha for path, sha in installed.items() if Path(path).name == Path(decoder).name)
    variants = [
        ("before", "aligned-bf16", BASE, old[decoder]),
        ("before", "aligned-fp16", BASE, old[decoder]),
        *(("after", name, model, patched_decoder) for name in cases),
    ]

    def one(spec):
        variant, name, image, sha = spec
        return run_case(package, variant, name, cases[name], image, sha, labels)

    with ThreadPoolExecutor(max_workers=2) as pool:
        for spec, row in zip(variants, pool.map(one, variants)):
            result.setdefault(spec[0], {})[spec[1]] = row
            write(SUITE / "result.json", result)
except (OSError, ValueError, AssertionError, StopIteration, subprocess.SubprocessError) as error:
    result.update(error=type(error).__name__, frame=traceback.extract_tb(error.__traceback__)[-1].lineno)
    if SUITE.is_dir():
        (SUITE / "failure.log").write_text(traceback.format_exc())
if SUITE.is_dir():
    write(SUITE / "result.json", result)
compact = {key: value for key, value in result.items() if key != "installed_paths"}
for variant in ("before", "after"):
    for row in compact.get(variant, {}).values():
        if "loaded" in row:
            row["loaded"] = len(row["loaded"])
print(json.dumps(compact, separators=(",", ":")))
