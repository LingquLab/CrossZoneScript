"""Read scalar Send fault, queue mappings and timer evidence without execution."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from vf_inspect import host

OBJECT_SHA = "6116bbb9ad0aa67ef0945aa40b6654d757239457e4678c485cc313a353d9aa89"


def inside_container():
    sys.path.insert(0, "/baseline")
    import sst
    root, folder = Path("/work"), Path("/work/scalar-send-34")
    report = root / "scalar-fault-34-1"
    report.mkdir()
    saved = json.loads((folder / "result.json").read_text())
    assert saved["object_sha256"] == OBJECT_SHA == sst.checksum(folder / "send.o")
    assert saved["stages"][0]["returncode"] == 255 and not saved["send_verified"]
    snapshot = json.loads(Path("/baseline/environment.snapshot.json").read_text())
    sst.verify_environment(snapshot)
    log = folder / "send-only/sim.log"
    with log.open("rb") as stream:
        stream.seek(max(0, log.stat().st_size - 4 * 1024 * 1024))
        text = stream.read().decode(errors="replace")
    fatals = []
    for line in text.splitlines():
        if "FATAL:" not in line:
            continue
        address = re.search(r"Unknown low level mem to access!\[addr=\s*(\d+)\s*\]", line)
        prefix = re.search(r"Unknown low level mem to access!\[addr=\s*(\d+)", line)
        component = re.search(r"\[Element:\s*([^\]]+)\]", line)
        cycle = re.search(r"\[Sim:\s*(\d+)\(cycle\)\]", line)
        fatals.append({"component": component[1][:100] if component else None,
                       "simulation_cycle": int(cycle[1]) if cycle else None,
                       "complete_address": int(address[1]) if address else None,
                       "address_prefix": prefix[1][:24] if prefix else None})
    workload = sst.yaml.safe_load((folder / "send-only/workload.yml").read_text())
    mappings = []
    for chip in workload["workload_config"].values():
        for name, record in chip["kernel_param"].items():
            if not name.startswith("load_") or not record.get("input_bin"):
                continue
            image = Path(record["input_bin"])
            mappings.append((int(record["value"]), int(record["value"]) + image.stat().st_size, name[5:]))
    selected_fatals = sorted(fatals, key=lambda value: value["complete_address"] is None)[:3]
    for fatal in selected_fatals:
        address = fatal["complete_address"]
        if address is not None:
            fatal["rank_and_offset"] = list(divmod(address, sst.rt.GM_BYTES))
            fatal["loaded_region"] = next((name for start, end, name in mappings if start <= address < end), None)
    symbols = subprocess.run(["readelf", "-sW", str(folder / "send.o")], check=True,
                             capture_output=True, text=True, timeout=15).stdout
    functions = []
    for line in symbols.splitlines():
        fields = line.split()
        if len(fields) >= 8 and fields[3] == "FUNC" and re.search(
                r"LocalPutBatch|LocalUrmaSend|Simulator|UDMAPoll|UDMARing|UDMAMte3|UDMAPublish", fields[7]):
            functions.append({"name": fields[7][:220], "size": int(fields[2], 0)})
    relocs = subprocess.run(["readelf", "-rW", str(folder / "send_rel.o")], check=True,
                            capture_output=True, text=True, timeout=15).stdout
    targets = {}
    for line in relocs.splitlines():
        if re.search(r"LocalPutBatch|LocalUrmaSend|Simulator|UDMAPoll|UDMARing|UDMAMte3", line):
            name = next((field for field in line.split() if field.startswith("_Z")), None)
            if name:
                targets[name[:160]] = targets.get(name[:160], 0) + 1
    queues = []
    if hasattr(sst.rt, "queue_addresses"):
        for peer in (1, 2):
            record = sst.rt.queue_addresses(0, 8, peer, peer)
            queues.append({"rank": 0, "peer": peer, "qp": peer,
                           **{key: record[key] for key in ("sq", "cq", "sq_head", "sq_tail", "count", "cq_tail", "db", "cq_db")}})
    clock_fields = []
    def clocks(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                current = path + "." + str(key)
                if re.search(r"clock|freq|timer", str(key), re.I) and isinstance(item, (str, int, float)):
                    clock_fields.append({"key": current[:180], "value": str(item)[:120]})
                clocks(item, current)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                clocks(item, path + "." + str(index))
    for name, record in snapshot["resources"].items():
        path = Path(record["path"])
        if path.suffix in (".yml", ".yaml", ".json") and path.stat().st_size < 4 * 1024 * 1024:
            clocks(sst.yaml.safe_load(path.read_text()), name)
    timer = []
    preprocessed = root / "vf-ir-inspection-1/mte-scalar_pointer_runtime64-17/save-temps"
    sources = list(preprocessed.glob("*.ccei"))
    if len(sources) == 1:
        source = sources[0].read_text(errors="replace")
        match = re.search(r"GetSystemCycle\s*\(\s*\)\s*\{([^}]{0,1200})\}", source)
        if match:
            timer = [line.strip()[:250] for line in match[0].splitlines()[:4]]
    bins = folder / "send-only/bin"
    output_files = [path for path in bins.glob("*.bin")] if bins.is_dir() else []
    out = {"read_only": True, "simulation_started": False, "recompiled": False,
           "object_sha256": OBJECT_SHA, "fatal_count_in_tail": len(fatals), "fatal_events": selected_fatals,
           "complete_fatal_address_count": sum(value["complete_address"] is not None for value in fatals),
           "posting_function_count": len(functions), "posting_functions": functions[:12],
           "call_relocation_targets": dict(list(targets.items())[:8]),
           "queue_mappings": queues, "runtime_source_sha256": hashlib.sha256(Path(sst.rt.__file__).read_bytes()).hexdigest(),
           "output_bin_count": len(output_files),
           "queue_state_dump_count": sum("queue_state" in path.name for path in output_files),
           "clock_config_fields": clock_fields[:10], "timer_source_snippets": timer}
    (root / "scalar-fault-result.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, separators=(",", ":")))


if __name__ == "__main__":
    try:
        if sys.argv[1] == "--container":
            inside_container()
        else:
            host(json.loads(sys.argv[1]), entry="scalar_fault.py", directory="scalar-fault-34-1", timeout=90)
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "detail": str(error)[:100]}))
        sys.exit(1)
