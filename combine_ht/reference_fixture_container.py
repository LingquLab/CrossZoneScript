"""Compare installed V1.1 perf/PV configurations with the published identical HT fixture."""
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import resource
import signal
import subprocess
import sys
import tarfile
import time

import yaml

sys.path.insert(0, "/work")
import sst

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
SHARED = ROOT / "shared"
ARCHIVE_SHA = "17a9ea0f7e8a5de2bb43a419df0ac64754f6fbbc9255f4a2e7e5920627f235dd"
KERNEL_SHA = "09b138b368c80227aa4d0a0a5dbf56d135f325044352f7608a0a8765191c9966"


def prepare():
    assert sst.checksum(ASSETS / "fixture.tar.gz") == ARCHIVE_SHA
    identity = json.loads((ASSETS / "identity.json").read_text())
    assert identity["kernel_sha256"] == KERNEL_SHA and identity["input_archive_sha256"] == ARCHIVE_SHA
    binary = gzip.decompress((ASSETS / "kernel.o.gz").read_bytes())
    assert len(binary) == 10485760 and hashlib.sha256(binary).hexdigest() == KERNEL_SHA
    SHARED.mkdir()
    with tarfile.open(ASSETS / "fixture.tar.gz", "r:gz") as archive:
        members = archive.getmembers()
        assert len(members) == 1603 and sum(member.size for member in members) < 512 * 1024 * 1024
        for member in members:
            rel = PurePosixPath(member.name)
            assert member.isfile() and not rel.is_absolute() and ".." not in rel.parts
            assert rel.parts[0] in {"input_bin", "fixture-data", "workload.yml", "export-layout.json"}
            path = SHARED / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as output:
                import shutil
                shutil.copyfileobj(archive.extractfile(member), output)
    kernel = SHARED / "kernel/inter_server_send.o"
    kernel.parent.mkdir()
    kernel.write_bytes(binary)
    text = (SHARED / "workload.yml").read_text()
    assert identity["runtime_root"] + "/" in text
    workload = yaml.safe_load(text.replace(identity["runtime_root"], str(SHARED)))
    assert len(workload["workload_config"]) == 64
    for rank in range(64):
        chip = workload["workload_config"]["chip" + str(rank)]
        assert chip["block_dim"] == 16 and chip["vec_func"] == sst.ENTRY
        assert chip["kernel_file"] == str(kernel)
        words = chip["kernel_param"]
        assert words["word10"]["value"] == 0, "published profiling-off object required"
        for field in words.values():
            if "input_bin" in field:
                assert Path(field["input_bin"]).is_file()
        for name in ("native_sq_ctx", "native_cq_ctx", "native_mem_info"):
            assert "load_" + name in words
    layout = json.loads((SHARED / "export-layout.json").read_text())
    assert len(layout["ranks"]) == 64
    for row in layout["ranks"]:
        chip = workload["workload_config"]["chip" + str(row["rank"])]
        loaded = chip["kernel_param"]["load_workspace"]
        workspace = Path(loaded["input_bin"]).read_bytes()
        target = row["exports"]["returned"]
        offset = target["addr"] - loaded["value"]
        assert workspace[offset:offset + target["size"]] == bytes(target["size"])
    (SHARED / "relocated-workload.yml").write_text(yaml.safe_dump(workload, sort_keys=False))
    return identity, workload, layout


def environment(profile, case):
    original = json.loads(Path("/work/environment.snapshot.json").read_text())
    config = json.loads(Path(original["settings"]["config_file"]).read_text())
    paths = {}
    for key, group in sst.RESOURCE_GROUPS.items():
        if group == "v310_deployment":
            candidates = sorted((Path("/opt/sim/simulator") / group).rglob(Path(key).name))
            selected = [path for path in candidates if profile + "_config" in path.parts]
            if len(selected) != 1:
                raise ValueError(profile + " configuration unavailable for " + key)
            paths[key] = str(selected[0])
        else:
            paths[key] = original["resources"][key]["path"]
    config["platform_resources"] = paths
    path = case / "selected-environment.json"
    sst.write_json(path, config)
    settings = sst.load_environment(path)
    resources = {key: Path(value) for key, value in paths.items()}
    snapshot = dict(settings=settings, resources={key: sst.file_record(value) for key, value in resources.items()},
                    runtime_files=sst.runtime_fingerprint(settings, resources))
    assert sst.checksum(Path(settings["library_dir"]) / "libDavinciSimulator.so") == sst.V11_DIE_DECODER_SHA256
    sst.verify_environment(snapshot)
    return snapshot


def scalar_modes(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "simt_urma_flag":
                yield value
            else:
                yield from scalar_modes(value)
    elif isinstance(node, list):
        for value in node:
            yield from scalar_modes(value)


def check(case, layout):
    actual = case / "actual"
    actual.mkdir()
    guards = 0
    for row in layout["ranks"]:
        rank = row["rank"]
        folder = actual / ("rank%02d" % rank)
        folder.mkdir()
        for name, target in row["exports"].items():
            payload = sst.read_output(case, rank, name, target["addr"], target["size"])
            (folder / (name + ".bin")).write_bytes(payload)
        for guard in row["guards"]:
            payload = sst.read_output(case, rank, guard["name"], guard["addr"], guard["size"])
            assert payload == bytes([guard["expected_byte"]]) * guard["size"], "workspace guard changed"
            guards += 1
    result = subprocess.run([sys.executable, str(ASSETS / "check.py"), "check", "--output", str(SHARED / "fixture-data"),
                             "--actual", str(actual)], capture_output=True, text=True, timeout=30)
    (case / "check.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, "published_checker_failed; inspect retained check.log"
    counts = json.loads(result.stdout)
    assert counts == dict(ranks=64, returned_values=262144, completion_slots=1024, output_values=32768, statuses=512)
    assert guards == 128
    return dict(counts, guards=guards)


def run(profile, workload, layout):
    case = ROOT / profile
    case.mkdir()
    snapshot = environment(profile, case)
    with (case / "model.log").open("w") as log, sst.redirect_stdout(log):
        model = sst.write_model(case, snapshot, workload, 64)
    transport = yaml.safe_load((case / "UBusSim_config/UBusSim_simulator_config.yml").read_text())
    modes = list(scalar_modes(transport))
    assert modes and all(value is False for value in modes), "SIMT URMA is prohibited"
    result = dict(profile=profile, report=str(case), qualified=False, simt_urma_flag=False, model_su=model["su"],
                  lsu_level=model["lsu_level"], kernel_sha256=KERNEL_SHA, fixture_archive_sha256=ARCHIVE_SHA,
                  workload_sha256=sst.checksum(case / "workload.yml"), model_config_hashes={}, model_modes={})
    document = yaml.safe_load((case / "v310_config/davidV100_simulator_config.yml").read_text())
    for name in ("su", "smmu", "aa", "udie_aa"):
        node = document["simulator_config"].get(name, {})
        result["model_modes"][name] = dict(component=node.get("component"), **{key: value for key, value in node.get("params", {}).items()
            if key in ("pv_mode", "agent_mode", "lsu_level", "enable_latency_model")})
    for path in sorted(case.glob("*_config/*.yml")):
        if path.name != "workload_config.yml":
            result["model_config_hashes"][str(path.relative_to(case))] = sst.checksum(path)
    command = sst.shell_command(snapshot["settings"], "sst", [snapshot["settings"]["sst_bin"], "-n", "8", case / "test_UBusSim.py"])
    result["started_utc"] = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    timed_out = False
    with (case / "sim.log").open("wb") as log:
        process = subprocess.Popen(command, cwd=case, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            process.wait(timeout=360)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=15)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    result.update(simulator_exit=process.returncode, timed_out=timed_out, sst_wall_s=time.monotonic()-start,
                  finished_utc=datetime.now(timezone.utc).isoformat(), cpu_user_s=after.ru_utime-usage.ru_utime,
                  cpu_system_s=after.ru_stime-usage.ru_stime)
    text = (case / "sim.log").read_text(errors="replace")
    result["completions"] = [match.group(0) for match in sst.SIM_TIME.finditer(text)][-1:]
    result["fatals"] = [line[:450] for line in text.splitlines() if "FATAL:" in line][:2]
    result["cqe_log_records"] = text.count("WrCQEResp")
    if result["simulator_exit"] == 0 and result["completions"] and not timed_out:
        try:
            result["checks"] = check(case, layout)
            result["qualified"] = True
        except (AssertionError, OSError, ValueError) as error:
            result["validation_error"] = str(error)[:200]
    sst.verify_environment(snapshot)
    sst.write_json(case / "run.json", result)
    return result


out = dict(diagnostic_only=True, experiment_completed=False, image_version="V1.1.0", cases=[])
try:
    identity, workload, layout = prepare()
    out["published_artifact_source"] = identity["commit"]
    out["equivalent_published_source"] = "211f7e97728781c4d7f01283c46e02872a34998f"
    for profile in ("perf", "pv"):
        out["cases"].append(run(profile, workload, layout))
        sst.write_json(ROOT / "summary.json", out)
    assert out["cases"][0]["workload_sha256"] == out["cases"][1]["workload_sha256"]
    out["experiment_completed"] = True
except Exception as error:
    out["error_type"] = type(error).__name__
    out["detail"] = str(error)[:300]
finally:
    sst.write_json(ROOT / "summary.json", out)
print(json.dumps(out))
sys.exit(0 if out["experiment_completed"] else 1)
