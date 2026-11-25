# Copyright (c) 2026 TileXR Project.
"""Capture rank 8 CQ memory and scalar instructions from the preserved bs16 joint case."""

import json
import os
import shutil
import signal
import struct
import subprocess
import time
from pathlib import Path

from exports import V310_DECODER_SHA256, digest, read_addressed

ROOT = Path("/work")
BASE = ROOT / "cases/64p-t16-pipeline-a4"
CASE = ROOT / "cases/64p-t16-pipeline-a4-cq-trace185us-a1"
RANK = 8
SLOTS = ((19, 23), (27, 23), (35, 23))
STRIDE = 0x2000000000
QSTATE = 0x1B00000000
PLANE = 64 * 40 * 64
STOP_AT = "185us"
WALL_LIMIT = 1200
DUMP_LIMIT = 4 * 1024**3
KERNEL = "ea66281bdd8796d68bca6b2dee16672f22f3ecd024fa7b82ddd1b98c9adfeaf6"
REVISION = "40596e2d9d454b7a7b00b4b34b96d49158e1c3a3"


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def cq_address(peer, slot):
    return 0x1A00000000 + (peer * 40 + slot) * 1048576


def capture_inputs():
    run = json.loads((BASE / "run.json").read_text())
    manifest = json.loads((BASE / "case-manifest.json").read_text())
    assert run["status"] == "timed_out" and run["kernel_sha256"] == KERNEL
    assert manifest["candidate_commit"] == REVISION and manifest["kernel_sha256"] == KERNEL
    assert digest(BASE / "kernel/inter_server_send.o") == KERNEL
    CASE.mkdir()
    for name, expected in manifest["files"].items():
        rel = Path(name)
        assert not rel.is_absolute() and ".." not in rel.parts
        source, target = BASE / rel, CASE / rel
        assert digest(source) == expected
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel.parts[0] in ("input_bin", "fixture-data", "kernel"):
            os.link(source, target)
        else:
            shutil.copyfile(source, target)


def configure_exports():
    workload = json.loads((CASE / "workload.yml").read_text().replace(str(BASE), str(CASE)))
    output = workload["workload_config"][f"chip{RANK}"]["output_param"]
    output["qstate"] = dict(addr=RANK * STRIDE + QSTATE, size=5 * PLANE)
    for peer, slot in SLOTS:
        output[f"cq{peer}"] = dict(addr=RANK * STRIDE + cq_address(peer, slot), size=512)
    write(CASE / "workload.yml", workload)
    shutil.copyfile(CASE / "workload.yml", CASE / "v310_config/workload_config.yml")

    # The same simulator configuration keys enabled rank-local scalar dumps in the bs4 trace.
    import yaml

    config = CASE / "v310_config/davidV100_simulator_config.yml"
    model = yaml.safe_load(config.read_text())
    model["architecture_config"]["chip_dump_enable_list"] = [RANK]

    def enable(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("enable_dump", "enable_dump_detail"):
                    value[key] = 1
                else:
                    enable(child)
        elif isinstance(value, list):
            for child in value:
                enable(child)

    enable(model)
    config.write_text(yaml.safe_dump(model, sort_keys=False))
    return digest(config)


def read_words(region, address, count):
    die = ((address >> 9) ^ (address >> 16) ^ (address >> 23)) & 1
    path = CASE / "bin" / f"david_{RANK}_die{die + 1}_{region}.bin"
    try:
        raw = read_addressed(path, address, 4 * count)
    except (FileNotFoundError, ValueError) as error:
        if isinstance(error, FileNotFoundError) or str(error).startswith("Missing dump bytes: "):
            return None
        raise
    return struct.unpack("<" + "I" * count, raw)


def summarize_queues():
    records = []
    for peer, slot in SLOTS:
        flat = peer * 40 + slot
        counters = {}
        for plane, key in enumerate(("count", "sqhead", "sqtail", "cqhead", "cqtail")):
            words = read_words("qstate", QSTATE + plane * PLANE + flat * 64, 1)
            counters[key] = None if words is None else words[0]
        entries = []
        for index in range(4):
            words = read_words(f"cq{peer}", cq_address(peer, slot) + index * 64, 2)
            if words is None:
                entries.append(None)
                continue
            first, second = words
            entries.append(dict(owner=(first >> 2) & 1, status=first >> 24,
                                substatus=(first >> 16) & 255, sq_entry=second & 65535))
        records.append(dict(peer=peer, slot=slot, cq_local=hex(cq_address(peer, slot)),
                            counters=counters, first_four_cqes=entries))
    return records


def dump_files():
    return [path for path in (CASE / "dumps").rglob("*") if path.is_file()]


def main():
    capture_inputs()
    configuration_sha256 = configure_exports()
    env = json.loads((CASE / "sst-environment.json").read_text())
    for key in ("sst_bin", "env_script", "decoder_library"):
        assert digest(env[key]) == env[key + "_sha256"]
    assert env["decoder_library_sha256"] == V310_DECODER_SHA256
    for name, expected in env.get("runtime_files", {}).items():
        assert digest(name) == expected

    record = dict(diagnostic_only=True, operator_qualified=False, input_source=str(BASE),
                  candidate_commit=REVISION, kernel_sha256=KERNEL, rank=RANK, stop_at=STOP_AT,
                  wall_limit_seconds=WALL_LIMIT, dump_limit_bytes=DUMP_LIMIT,
                  configuration_sha256=configuration_sha256, started_unix=time.time())
    report = CASE / "diagnostic.json"
    write(report, record)
    command = ["bash", "-c", 'set -e; source "$1"; shift; exec "$@"', "bash", env["env_script"],
               env["sst_bin"], "-n", "8", f"--stop-at={STOP_AT}", str(CASE / "test_UBusSim.py")]
    started = time.monotonic()
    with (CASE / "sim.log").open("wb") as log:
        child = subprocess.Popen(command, cwd=CASE, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        record["pid"] = child.pid
        write(report, record)
        while child.poll() is None:
            total = sum(path.stat().st_size for path in dump_files())
            if time.monotonic() - started >= WALL_LIMIT or total >= DUMP_LIMIT:
                record["stop_reason"] = "dump_limit" if total >= DUMP_LIMIT else "wall_limit"
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=10)
                break
            time.sleep(2)

    paths = dump_files()
    record.update(sst_exit=child.returncode, sst_wall_seconds=time.monotonic() - started,
                  finished_unix=time.time(), dump_files=len(paths),
                  scalar_detail_files=sum(path.name.endswith("_sudetail.dump") for path in paths),
                  dump_bytes=sum(path.stat().st_size for path in paths))
    write(report, record)
    if child.returncode == 0 and "stop_reason" not in record:
        record["queues"] = summarize_queues()
        write(report, record)
    print(json.dumps(record, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
