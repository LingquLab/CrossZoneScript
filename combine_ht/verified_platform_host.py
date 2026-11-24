"""Replay the original passed local HT platform; isolate model-image compatibility."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import time

import yaml

ROOT = Path(__file__).resolve().parent
WORK = ROOT.parents[1]
ORIGINAL = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-local-ht-fp32-09b138b3")
V11 = "tilexr-combine-ht-a6ebc0aa-verified-v11"
V10 = "tilexr-combine-ht-a6ebc0aa-verified-v10"
IMAGES = {"v11": "sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f",
          "v10": "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"}
DECODERS = {"v11": "bf93fd6d0749fc3c6eaae365c7cba558988e1fa25c1b9a26e139be61b60a3da3",
            "v10": "6d4cd3b729177646ca1c43d57971e5266a946405b6f820f3d0d914114b7ccfce"}
KERNEL = "09b138b368c80227aa4d0a0a5dbf56d135f325044352f7608a0a8765191c9966"
CONFIGS = ("v310_config/davidV100_simulator_config.yml", "v310_config/daw_config.yml",
           "v310_config/ddie_noc_config.yml", "v310_config/udie_noc_config.yml",
           "UBusSim_config/UBusSim_simulator_config.yml", "UBusSim_config/UBusSim_simulator_config_topo.yml")


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write(path, data):
    Path(path).write_text(json.dumps(data, indent=2) + "\n")


def call(argv, timeout=30, payload=None):
    result = subprocess.run(list(map(str, argv)), input=payload, capture_output=True, timeout=timeout)
    with (ROOT / "host.log").open("ab") as log:
        log.write(result.stdout + result.stderr)
    return result


def reserve_original():
    record = json.loads((ORIGINAL / "run.json").read_text())
    manifest = json.loads((ORIGINAL / "case-manifest.json").read_text())
    assert record["status"] == "sst_and_checker_passed" and record["returncode"] == 0
    assert manifest["kernel_sha256"] == sha(ORIGINAL / "kernel/inter_server_send.o") == KERNEL
    files = manifest["files"]
    assert len(files) < 5000
    shared = ROOT / "original-inputs"
    shared.mkdir()
    total = 0
    for name, expected in files.items():
        rel = PurePosixPath(name)
        assert not rel.is_absolute() and ".." not in rel.parts
        assert rel.parts[0] not in ("bin", "actual", "OUTPUT", "dumps", "runs")
        assert name not in ("run.json", "sim.log", "check.log", "native-runner.log", "e2e-summary.json")
        source = ORIGINAL / rel
        assert not source.is_symlink() and source.resolve().is_relative_to(ORIGINAL.resolve())
        assert sha(source) == expected, "original manifest mismatch: " + name
        total += source.stat().st_size
        assert total < 1024 * 1024 * 1024
        dest = shared / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    write(ROOT / "original-case-manifest.json", manifest)
    assert sha(shared / "kernel/inter_server_send.o") == KERNEL
    return manifest, shared


def transport_modes(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "simt_urma_flag":
                yield value
            else:
                yield from transport_modes(value)
    elif isinstance(node, list):
        for value in node:
            yield from transport_modes(value)


def clone_case(version, manifest, shared):
    case = ROOT / version
    case.mkdir()
    for name in manifest["files"]:
        source, target = shared / name, case / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name.startswith(("input_bin/", "fixture-data/")) or name == "kernel/inter_server_send.o":
            target.hardlink_to(source)
        else:
            shutil.copyfile(source, target)
    container_case = "/work/" + str(case.relative_to(WORK))
    workload = yaml.safe_load((case / "workload.yml").read_text())
    original_prefix = str(Path(workload["workload_config"]["chip0"]["kernel_file"]).parent.parent)
    assert Path(original_prefix).name == ORIGINAL.name
    for name in ("workload.yml", "v310_config/workload_config.yml"):
        text = (case / name).read_text()
        assert original_prefix + "/" in text
        (case / name).write_text(text.replace(original_prefix, container_case))
    for name in CONFIGS:
        assert sha(case / name) == sha(ORIGINAL / name)
    modes = list(transport_modes(yaml.safe_load((case / CONFIGS[-2]).read_text())))
    assert modes and all(value is False for value in modes), "SIMT URMA is prohibited"
    for name in manifest["fixture_files"]:
        assert sha(case / "fixture-data" / name) == manifest["fixture_files"][name]
    return case, container_case, original_prefix


FINGERPRINT = r'''
import hashlib,json,pathlib,sys
case=pathlib.Path(sys.argv[1]); version=sys.argv[2]; old_prefix=sys.argv[3]
def sha(path):return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
path=case/"sst-environment.json"
environment=json.loads(path.read_text())
changed=[]
for key in ("sst_bin","env_script","decoder_library"):
    actual=sha(environment[key]); field=key+"_sha256"
    if actual!=environment[field]:
        assert version=="v11","original image environment mismatch: "+key
        changed.append({"field":field,"original":environment[field],"actual":actual})
        environment[field]=actual
runtime={}
for name,expected in environment.get("runtime_files",{}).items():
    target=name
    actual=sha(target)
    if actual!=expected:
        assert version=="v11" and target.startswith("/opt/sim/"),"original runtime dependency mismatch"
        changed.append({"field":target,"original":expected,"actual":actual})
    runtime[target]=actual
environment["runtime_files"]=runtime
known={"v11":"bf93fd6d0749fc3c6eaae365c7cba558988e1fa25c1b9a26e139be61b60a3da3","v10":"6d4cd3b729177646ca1c43d57971e5266a946405b6f820f3d0d914114b7ccfce"}
assert environment["decoder_library_sha256"]==known[version],"decoder identity is unverified"
path.write_text(json.dumps(environment,indent=2)+"\n")
if version=="v11":
    export=case/"adapter/exports.py"
    text=export.read_text()
    assert text.count(known["v10"])==1,"original decoder guard differs"
    export.write_text(text.replace(known["v10"],known["v11"]))
manifest_path=case/"case-manifest.json"
manifest=json.loads(manifest_path.read_text())
manifest["files"]={name:sha(case/name) for name in manifest["files"]}
manifest_path.write_text(json.dumps(manifest,indent=2)+"\n")
libraries={str(p):sha(p) for p in (pathlib.Path(environment["decoder_library"]),pathlib.Path("/opt/sim/plugins/libDavinciSimulatorV310.so"),pathlib.Path("/opt/sim/plugins/libUBusSimulator.so"))}
record={"version":version,"updated_dependency_count":len(changed),"decoder_guard_updated":version=="v11","runtime_libraries":libraries,"sst_bin":environment["sst_bin"],"env_script":environment["env_script"]}
(case/"probe-runtime-identity.json").write_text(json.dumps(record,indent=2)+"\n")
print(json.dumps(record))
'''


def run(version, manifest, shared):
    name = V11 if version == "v11" else V10
    case, inside, original_prefix = clone_case(version, manifest, shared)
    write(case / "case-manifest.json", manifest)
    result = call(["docker", "exec", "-i", name, "python3", "-", inside, version, original_prefix],
                  payload=FINGERPRINT.encode(), timeout=90)
    assert result.returncode == 0, "original environment compatibility check failed; inspect retained host.log"
    fingerprint = json.loads(result.stdout)
    write(case / "probe-runtime-identity.json", fingerprint)
    args = ["python3", inside + "/adapter/run.py", "--case", inside, "--fixture", inside + "/fixture-data",
            "--checker", inside + "/source/tools/unitest/simulator/experiments/inter_server_send/fixture.py",
            "--environment", inside + "/sst-environment.json", "--threads", "8", "--timeout", "360"]
    command = ["docker", "exec", name, "timeout", "--kill-after=15s", "530s", "bash", "-c",
               'set -e; source /opt/sim/cann-env.sh; exec "$@"', "bash", *args]
    start = time.monotonic()
    started = datetime.now(timezone.utc).isoformat()
    with (case / "native-runner.log").open("wb") as log:
        process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=600)
    record = json.loads((case / "run.json").read_text()) if (case / "run.json").is_file() else {}
    text = (case / "sim.log").read_text(errors="replace") if (case / "sim.log").is_file() else ""
    result = dict(image_version=version, image_id=IMAGES[version], report=inside,
                  runner_exit=process.returncode, runner_wall_s=time.monotonic()-start,
                  started_utc=started, finished_utc=datetime.now(timezone.utc).isoformat(),
                  run_status=record.get("status"), simulator_exit=record.get("returncode"),
                  qualified=process.returncode==0 and record.get("status")=="sst_and_checker_passed",
                  kernel_sha256=sha(case / "kernel/inter_server_send.o"), simt_urma_flag=False,
                  config_hashes={name:sha(case/name) for name in CONFIGS},
                  normalized_workload_sha256=hashlib.sha256((case/"workload.yml").read_text().replace(inside,"$CASE").encode()).hexdigest(),
                  model_entry_sha256=sha(case / "test_UBusSim.py"), installed_model_sha256=sha(case / "installed_model.py"),
                  completions=re.findall(r"Simulation is complete, simulated time: [0-9.eE+-]+ [munpf]?s",text)[-1:],
                  fatals=[line[:450] for line in text.splitlines() if "FATAL:" in line][:2],
                  cqe_log_records=text.count("WrCQEResp"), runtime_identity=fingerprint)
    if "guards" in record:
        result["guards"] = record["guards"]
    if result["qualified"]:
        counts=json.loads((case/"check.log").read_text())
        assert counts==dict(ranks=64,returned_values=262144,completion_slots=1024,output_values=32768,statuses=512)
        result["checks"]=counts
    write(case / "probe-summary.json", result)
    return result


out = dict(diagnostic_only=True, experiment_completed=False, original=str(ORIGINAL), cases=[])
created = []


def create_container(version):
    name = V11 if version == "v11" else V10
    p = call(["docker", "create", "--name", name, "--runtime=runc", "--network=none", "--cap-drop=ALL",
              "--security-opt=no-new-privileges", "--label", "crosszone.session=a6ebc0aa-6012-4c7c-8d03-e69391a0317f",
              "--cpus=8", "--memory=32g", "--pids-limit=1024", "--shm-size=64m", "--ulimit", "nofile=524288:524288",
              "--mount", "type=bind,src="+str(WORK)+",dst=/work",
              "--mount", "type=bind,src="+str(ORIGINAL.parent)+",dst=/work/inter-server-native,readonly",
              IMAGES[version], "sleep", "infinity"])
    assert p.returncode == 0, "isolated reference container create failed"
    created.append(name)
    assert call(["docker", "start", name]).returncode == 0
    check = call(["docker", "exec", name, "python3", "-c",
                  'import glob; assert not any(glob.glob(p) for p in ("/dev/davinci*","/dev/devmm*","/dev/hisi_hdc*"))'])
    assert check.returncode == 0, "CPU-only reference container required"


try:
    assert WORK == Path("/var/tmp/tilexr-combine-ht-f71d0252-ef1e-4b5c-b299-c2429ebcfbdf-v11")
    manifest, shared = reserve_original()
    out["original_manifest_sha256"] = sha(ORIGINAL / "case-manifest.json")
    create_container("v11")
    result = run("v11", manifest, shared)
    out["cases"].append(result)
    write(ROOT / "summary.json", out)
    if not result["qualified"]:
        create_container("v10")
        out["cases"].append(run("v10", manifest, shared))
        assert out["cases"][0]["config_hashes"] == out["cases"][1]["config_hashes"]
        assert out["cases"][0]["normalized_workload_sha256"] == out["cases"][1]["normalized_workload_sha256"]
        assert out["cases"][0]["model_entry_sha256"] == out["cases"][1]["model_entry_sha256"]
    for name, expected in manifest["files"].items():
        assert sha(ORIGINAL/name)==expected,"original shared case changed"
    out["experiment_completed"] = True
except Exception as error:
    out["error_type"] = type(error).__name__
    out["detail"] = str(error)[:350]
finally:
    out["reference_containers_removed"] = {name:call(["docker","rm","-f",name],timeout=45).returncode == 0 for name in created}
    write(ROOT / "summary.json", out)
print(json.dumps(out))
raise SystemExit(0 if out["experiment_completed"] else 1)
