"""Change only one WQE's QP registration while reusing qualified diagnostic ELFs."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import struct
import subprocess
import sys
import time

import yaml

sys.path.insert(0, "/work")
import sst

ROOT = Path(__file__).resolve().parent
CASES = (
    ("stdev-qp2", Path("/work/diagnostics/mmio-width-a1/mmio32"),
     "64176c41799af58382d606aa024dfbb7a7956d04aaba7ba93249d452db7ce9fd"),
    ("mte-qp2", Path("/work/diagnostics/mmio-mte-a1/mmio-mte4"),
     "7695b470022e748cde09fceac23939f45021bc5e54086e1772a5fefec5ae48c5"),
)


def save(value):
    sst.write_json(ROOT / "summary.json", value)


def modes(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "simt_urma_flag":
                yield value
            else:
                yield from modes(value)
    elif isinstance(node, list):
        for value in node:
            yield from modes(value)


def run_case(name, prior, expected_hash):
    baseline = json.loads((prior / "run.json").read_text())
    assert baseline["simulator_exit"] == 255 and not baseline["timed_out"]
    assert sst.checksum(prior / "probe.o") == expected_hash
    case = ROOT / name
    case.mkdir()
    for directory in ("v310_config", "UBusSim_config"):
        shutil.copytree(prior / directory, case / directory)
    for filename in ("installed_model.py", "test_UBusSim.py", "environment.snapshot.json", "probe.o"):
        shutil.copyfile(prior / filename, case / filename)
    config_hashes = {}
    for original in sorted(prior.glob("*_config/*.yml")):
        if original.name == "workload_config.yml":
            continue
        rel = original.relative_to(prior)
        assert sst.checksum(original) == sst.checksum(case / rel)
        config_hashes[str(rel)] = sst.checksum(original)
    transport_modes = list(modes(yaml.safe_load((case / "UBusSim_config/UBusSim_simulator_config.yml").read_text())))
    assert transport_modes and all(value is False for value in transport_modes)
    workload = yaml.safe_load((prior / "workload.yml").read_text())
    old_workload = copy.deepcopy(workload)
    chip = workload["workload_config"]["chip3"]
    params = chip["kernel_param"]
    prefill_param = params["load_prefill"]
    original_prefill = Path(prefill_param["input_bin"]).read_bytes()
    changed = bytearray(original_prefill)
    wqe_offset = params["image"]["value"] - prefill_param["value"]
    assert 0 <= wqe_offset <= len(changed) - 64
    mem = Path(params["load_mem_info"]["input_bin"]).read_bytes()
    old_flat, new_flat = 2 * sst.rt.HARDWARE_QPS, 2 * sst.rt.HARDWARE_QPS + 2
    old_memory, new_memory = mem[old_flat * 40:(old_flat + 1) * 40], mem[new_flat * 40:(new_flat + 1) * 40]
    old_tid = struct.unpack_from("<I", old_memory, 8)[0]
    new_tid = struct.unpack_from("<I", new_memory, 8)[0]
    assert old_memory[:8] == new_memory[:8] and old_memory[12:32] == new_memory[12:32]
    old_word = struct.unpack_from("<I", changed, wqe_offset + 12)[0]
    assert old_word & 0xFFFFF == old_tid and old_tid != new_tid
    struct.pack_into("<I", changed, wqe_offset + 12, (old_word & ~0xFFFFF) | new_tid)
    difference = [index for index, pair in enumerate(zip(original_prefill, changed)) if pair[0] != pair[1]]
    assert difference and all(wqe_offset + 12 <= index < wqe_offset + 16 for index in difference)
    patched = case / "prefill-qp2.bin"
    patched.write_bytes(changed)
    prefill_param["input_bin"] = str(patched)
    for node in workload["workload_config"].values():
        assert node["kernel_file"] == str(prior / "probe.o")
        node["kernel_file"] = str(case / "probe.o")
    # Normalize relocated file paths, then prove there were no other workload changes.
    normalized = copy.deepcopy(workload)
    for key, node in normalized["workload_config"].items():
        node["kernel_file"] = old_workload["workload_config"][key]["kernel_file"]
    normalized["workload_config"]["chip3"]["kernel_param"]["load_prefill"]["input_bin"] = old_workload["workload_config"]["chip3"]["kernel_param"]["load_prefill"]["input_bin"]
    assert normalized == old_workload
    text = yaml.safe_dump(workload, sort_keys=False)
    (case / "workload.yml").write_text(text)
    (case / "v310_config/workload_config.yml").write_text(text)
    snapshot = json.loads((case / "environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    command = sst.shell_command(snapshot["settings"], "sst", [snapshot["settings"]["sst_bin"], "-n", "32", case / "test_UBusSim.py"])
    started = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    timed_out = False
    with (case / "sim.log").open("wb") as log:
        process = subprocess.Popen(command, cwd=case, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            process.wait(timeout=150)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=15)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    text = (case / "sim.log").read_text(errors="replace")
    result = dict(name=name, diagnostic_only=True, report=str(case), object_sha256=expected_hash,
                  only_semantic_change="one WQE remote registration QP0 to QP2", changed_bytes=len(difference),
                  old_tid=old_tid, new_tid=new_tid, wqe_offset=wqe_offset, simt_urma_flag=False,
                  baseline_report=str(prior), baseline_exit=baseline["simulator_exit"],
                  baseline_fatals=baseline["fatals"], simulator_exit=process.returncode, timed_out=timed_out,
                  started_utc=started, finished_utc=datetime.now(timezone.utc).isoformat(),
                  wall_s=time.monotonic() - start, cpu_user_s=after.ru_utime-usage.ru_utime,
                  cpu_system_s=after.ru_stime-usage.ru_stime,
                  completions=[match.group(0) for match in sst.SIM_TIME.finditer(text)][-1:],
                  fatals=[line[:450] for line in text.splitlines() if "FATAL:" in line][:2],
                  model_config_hashes=config_hashes, qualified=False)
    signature = lambda value: value.split("FATAL:", 1)[-1].strip()
    result["first_failure_unchanged"] = bool(result["fatals"] and baseline["fatals"] and
        signature(result["fatals"][0]) == signature(baseline["fatals"][0]))
    if process.returncode == 0 and result["completions"] and not timed_out:
        marker = params["marker"]["value"]
        raw = sst.read_output(case, 3, "probe", marker, 32)
        expected_marker = bytearray(Path(params["load_probe"]["input_bin"]).read_bytes())
        struct.pack_into("<3I", expected_marker, 0, 0, 1, 1)
        result["marker_and_guards_match"] = raw == expected_marker
        destination = struct.unpack_from("<Q", changed, wqe_offset + 40)[0]
        length, token, source = struct.unpack_from("<IIQ", changed, wqe_offset + 48)
        assert length == 512 and token == 0 and destination % 512 == source % 512 == 0
        source_param = params["load_input"]
        source_image = Path(source_param["input_bin"]).read_bytes()
        source_offset = source - source_param["value"]
        assert 0 <= source_offset <= len(source_image) - length
        payload = source_image[source_offset:source_offset + length]
        checks = []
        for rank in range(8):
            node = workload["workload_config"]["chip" + str(rank)]
            output = node["output_param"]["workspace"]
            expected = bytearray(Path(node["kernel_param"]["load_workspace"]["input_bin"]).read_bytes()[:output["size"]])
            if output["addr"] <= destination < output["addr"] + output["size"]:
                offset = destination - output["addr"]
                assert rank == 2 and offset + length <= len(expected)
                expected[offset:offset + length] = payload
            actual = sst.read_output(case, rank, "workspace", output["addr"], output["size"])
            checks.append(actual == expected)
        result["all_workspace_bytes_match_one_payload"] = all(checks)
        result["qualified"] = raw == expected_marker and all(checks)
    sst.write_json(case / "run.json", result)
    return result


out = dict(diagnostic_only=True, experiment_completed=False, image_version="V1.1.0", cases=[])
try:
    assert sst.checksum(sst.KERNEL) == "519c9fba13122634af8f58c50947e1765e1627b6fa6263a312116fbce0bfb790"
    for definition in CASES:
        out["cases"].append(run_case(*definition))
        save(out)
    out["experiment_completed"] = True
except Exception as error:
    out["error_type"] = type(error).__name__
    out["detail"] = str(error)[:300]
finally:
    save(out)
print(json.dumps(out))
sys.exit(0 if out["experiment_completed"] else 1)
