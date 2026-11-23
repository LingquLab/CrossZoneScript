"""Bind a verified Full reference to a new candidate object and launch arguments."""

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path
import yaml

record = json.loads(sys.argv[1])
contract = record["contract"]
reference = Path(contract["reference"])
candidate = Path(record["kernel"])
case = Path(record["case"])


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            checksum.update(block)
    return checksum.hexdigest()


def write_json(path, document):
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def validate(root):
    environment = dict(os.environ)
    environment["PYTHONPATH"] = f"{root}:{record['generator']}"
    subprocess.run([sys.executable, "-B", str(root / "case_tools.py"), "preflight",
                    "--case-dir", str(root)], check=True, cwd=root, env=environment, timeout=90)


manifest_file = reference / "manifest.json"
scenario_file = reference / "scenario.json"
assert digest(manifest_file) == contract["manifest_sha256"], "reference manifest hash"
assert digest(scenario_file) == contract["scenario_sha256"], "reference scenario hash"
assert digest(candidate) == record["kernel_sha256"], "candidate object hash"
validate(reference)

manifest = json.loads(manifest_file.read_text())
scenario = json.loads(scenario_file.read_text())
assert scenario["rank_size"] == 64 and scenario["block_dim"] == 31
assert scenario["elements_per_peer"] * 4 == contract["bytes_per_peer"]
assert scenario["copyout_enabled"] is (record["kind"] == "functional")
kernel_paths = [p for p in (reference / "kernel").glob("*.o")
                if digest(p) == contract["kernel_sha256"]]
if len(kernel_paths) != 1:
    raise ValueError("the reference must contain one Full object matching the seed")
kernel_rel = kernel_paths[0].relative_to(reference)
case.mkdir()
files = set(manifest["files"]) | {
    "scenario.json", "case_tools.py", "full_validation.py", "jetty_status.py", "profiling_view.py",
    "test_UBusSim.py", "workload.yml", "environment.snapshot.json", str(kernel_rel),
}
for name in sorted(files):
    source = reference / name
    if not source.resolve().is_relative_to(reference.resolve()) or source.is_symlink():
        raise ValueError("reference file escapes the case: " + name)
    target = case / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
shutil.copyfile(candidate, case / kernel_rel)

checker = case / "full_validation.py"
source = checker.read_text()
event_pairs = "pairs = {2: 1, 11: 10, 13: 12, 21: 20, 31: 30, 41: 40}"
allowed_events = "allowed = {1, 2, 10, 11, 12, 13, 20, 21, 30, 31, 40, 41}"
sender_events = "{30, 31, 40, 41} for e in ids[2:-4]"
if any(source.count(value) != 1 for value in (event_pairs, allowed_events, sender_events)):
    raise ValueError("unrecognized Full profiling checker")
source = source.replace(event_pairs, event_pairs[:-1] + ", 51: 50, 53: 52}")
source = source.replace(allowed_events, allowed_events[:-1] + ", 50, 51, 52, 53}")
source = source.replace(sender_events, "{30, 31, 40, 41, 50, 51, 52, 53} for e in ids[2:-4]")
compile(source, str(checker), "exec")
checker.write_text(source)

old_paths = (scenario["kernel"]["original_path"], str(reference / kernel_rel))
changes = {
    **{path: str(case / kernel_rel) for path in old_paths},
    contract["kernel_sha256"]: record["kernel_sha256"],
    str(reference): str(case),
}
for path in [case / "workload.yml", case / "test_UBusSim.py",
             *(case / "v310_config").rglob("*.yml"), *(case / "UBusSim_config").rglob("*.yml")]:
    text = path.read_text()
    modified = text
    for old, new in changes.items():
        modified = modified.replace(old, new)
    if modified != text:
        path.write_text(modified)
workload = (case / "workload.yml").read_text()
if kernel_rel.name not in workload and contract["entry"] not in workload:
    raise ValueError("workload does not identify the Full kernel")

if record["schedule"] == "clock":
    args = sorted((case / "input_bin").glob("rank*/kernel_args.bin"))
    if len(args) != 64:
        raise ValueError("expected all 64 launch parameter images")
    old_deadlines = {}
    for path in args:
        data = bytearray(path.read_bytes())
        if len(data) != contract["abi_size"]:
            raise ValueError("unexpected launch ABI in " + path.parent.name)
        rank = int(path.parent.name.removeprefix("rank"))
        old_deadlines[rank] = struct.unpack_from("<Q", data, 48)[0]
        struct.pack_into("<Q", data, 48, record["deadline"])
        path.write_bytes(data)
    for relative_path in ("workload.yml", "v310_config/workload_config.yml"):
        path = case / relative_path
        workload_config = yaml.safe_load(path.read_text())
        chips = workload_config["workload_config"]
        if set(chips) != {f"chip{rank}" for rank in old_deadlines}:
            raise ValueError("unexpected Full workload ranks")
        for rank, original in old_deadlines.items():
            params = chips[f"chip{rank}"]["kernel_param"].values()
            fields = [param for param in params if int(str(param["param_offset"]), 0) == 48]
            if (len(fields) != 1 or "input_bin" in fields[0] or
                    int(str(fields[0]["value"]), 0) != original):
                raise ValueError("Full clock ABI binding mismatch for rank " + str(rank))
            field = fields[0]
            field["value"] = str(record["deadline"]) if isinstance(field["value"], str) else record["deadline"]
        path.write_text(yaml.safe_dump(workload_config, sort_keys=False))

scenario = json.loads(json.dumps(scenario).replace(str(reference), str(case)))
scenario["candidate_revision"] = record["revision"]
scenario["kernel"].update(original_path=str(case / kernel_rel), sha256=record["kernel_sha256"])
scenario["kernel"]["manifest"].update(sha256=record["kernel_sha256"], copyout=scenario["copyout_enabled"])
scenario["two_group"] = {"schedule": record["schedule"], "deadline_cycles": record["deadline"]}
write_json(case / "scenario.json", scenario)
manifest["source_commit"] = record["revision"]
manifest["files"] = {name: digest(case / name) for name in sorted(files)}
write_json(case / "manifest.json", manifest)
validate(case)
print("PREPARED_CASE_BOUND " + str(case))
