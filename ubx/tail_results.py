"""Summarize completed Dispatch cases and package the detailed route evidence."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import sys

from stage_sources import connect, run_remote
from tail_queue import AB_NAMESPACE, ASSIST_NAMESPACE


COLLECT = r'''
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(sys.argv[1])
def read(name):
    path = root / name
    return json.loads(path.read_text()) if path.is_file() else {}


def compact(packet):
    tail = packet["tail"]
    return {
        "round": packet["round"], "native_us": packet["native_us"], "tail_cores": [tail["producer"], tail["consumer"]],
        "last_ready_us": round(packet["last_ready"], 3),
        "producer_drain_us": round(tail["producer_drain_us"], 3), "recv_drain_us": round(tail["recv_drain_us"], 3),
        "flag_to_submit_us": tail["relay_flag_to_mem_submit_us"],
        "late_probe": tail["drain_to_positive_probe_issue"],
        "prior_probe": tail["drain_to_previous_probe_issue"],
        "copies_before_detect": tail["other_copyouts_after_producer_drain_before_detect"],
        "detect_to_load_us": tail["detected_to_load"], "load_to_issue_us": tail["load_to_copyout_issue"],
        "same_core_records": packet["same_core_records"], "prefix_us": packet["slow_rank_prefix_us"],
        "copyout_rows_by_role": packet.get("copyout_rows_by_role", {}),
        "relay_token_totals": [packet["single_route"]["tokens"], packet["multi_route"]["tokens"],
                               packet["multi_route"]["split_tokens"]],
    }


state = read("status.json")
progress = read("report-status.json")
results = {}
inputs = {}
for case in read("matrix.json"):
    impl, _, bs, mode = case
    name = "-".join((impl, "dispatch", f"bs{bs}", mode))
    folder = Path("runs", name)
    timing = read(folder / "timing.json")
    if timing:
        inputs.setdefault(bs, set()).add(timing["route_sha256"])
        result = {"native_us": timing["native_task_us"]["round_slowest_rank"],
                  "abi": timing["abi"], "backend": timing["backend"]}
        if mode == "on":
            reports = timing["packet_diagnostics"]
            result["peak"] = compact(reports["max"])
            result["median"] = {"native_us": reports["median_high"]["native_us"],
                                "last_ready_us": reports["median_high"]["last_ready"],
                                "prefix_us": reports["median_high"]["slow_rank_prefix_us"]}
        results[name] = result
if any(len(hashes) != 1 for hashes in inputs.values()):
    raise ValueError("A/B runs used different route inputs")

answer = {"phase": state.get("phase"), "case": state.get("case"), "report_state": progress,
          "results": results}
if sys.argv[2] == "pack" and state.get("phase") == "completed" and len(results) + len(progress["failed_cases"]) == state["cases"]:
    target = root / sys.argv[3]
    temporary = target.with_suffix(".part")
    with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=3) as archive:
        for path in (root / "runs").glob("*"):
            for file in (path / "timing.json", path / "failure.json", *sorted((path / "tail-report").glob("*.json"))):
                if file.is_file():
                    archive.write(file, file.relative_to(root))
        for name in ("matrix.json", "status.json", "report-status.json", "restore_expert_copy.patch"):
            if (root / name).is_file():
                archive.write(root / name, name)
        archive.writestr("comparison.json", json.dumps(answer, indent=2) + "\n")
    with ZipFile(temporary) as archive:
        if archive.testzip():
            raise ValueError("invalid Dispatch evidence archive")
    temporary.replace(target)
    with target.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    answer.update(bundle=str(target), sha256=digest)
print(json.dumps(answer, separators=(",", ":")))
'''


def main():
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    assist = args.get("assist", False)
    namespace = ASSIST_NAMESPACE if assist else AB_NAMESPACE
    filename = ("TileXR-UBX-EP32-Dispatch-assist-e8ee4fff.zip" if assist else
                "TileXR-UBX-EP32-Dispatch-tail-AB.zip")
    with connect(args["host"]) as client, client.open_sftp() as transfer:
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / namespace
        mode = "pack" if args.get("pack") else "status"
        cmd = "/usr/bin/python3 -B - " + shlex.join([str(root), mode, filename])
        code, out, _ = run_remote(client, cmd, timeout=200, input_text=COLLECT)
        if code:
            raise RuntimeError("could not verify Dispatch A/B evidence")
        result = json.loads(out)
        if "bundle" in result:
            transfer.get_channel().settimeout(100)
            target = Path(os.environ["USERPROFILE"]) / "Downloads" / PurePosixPath(result["bundle"]).name
            temporary = target.with_suffix(".part")
            transfer.get(result["bundle"], str(temporary))
            with temporary.open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != result["sha256"]:
                    raise ValueError("downloaded A/B report checksum differs")
            temporary.replace(target)
            result["green_windows_bundle"] = str(target)
        print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
