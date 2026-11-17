"""Select numeric route, context and failure fields from an existing two-rank run."""

import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
import sys


args = json.loads(base64.urlsafe_b64decode(sys.argv[1]))
assert re.fullmatch(r"pr229-[A-Za-z0-9_-]{1,58}", args["run_id"])
parent = Path("/home/c00996840/ai/TileXR-test").resolve(strict=True)
work = (parent / args["run_id"]).resolve(strict=True)
assert work.parent == parent
result = json.loads((work / "result.json").read_text())
assert result["devices"] == [2, 3]
recovery_state = {}
if args.get("recovery_state", False):
    def occupancy(device):
        record = {"device": device, "state": "indeterminate"}
        try:
            probe = subprocess.run(["npu-smi", "info", "-t", "proc-mem", "-i", str(device)],
                                   capture_output=True, text=True, timeout=10)
            record["query_exit_code"] = probe.returncode
            record["process_ids"] = [int(value) for value in
                                     re.findall(r"Process id\s*:\s*(\d+)", probe.stdout)][:16]
            if probe.returncode == 0:
                if record["process_ids"]:
                    record["state"] = "busy"
                elif re.search(r"NPU ID\s*:\s*" + str(device) + r"(?:\s|$)", probe.stdout):
                    record["state"] = "idle"
        except subprocess.TimeoutExpired:
            record["query_timed_out"] = True
        except OSError as error:
            record["query_error_type"] = type(error).__name__
        return record

    with ThreadPoolExecutor(max_workers=2) as pool:
        recovery_state["devices"] = list(pool.map(occupancy, [2, 3]))
    owned_processes = []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.joinpath("cwd").resolve(strict=True) != work:
                continue
            name = process.joinpath("comm").read_text().strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name):
                continue
            status = process.joinpath("status").read_text()
            state = re.search(r"^State:\s*(\w+)", status, re.M)
            parent_id = re.search(r"^PPid:\s*(\d+)", status, re.M)
            owned_processes.append({"pid": int(process.name), "name": name,
                                    "state": state[1] if state else "unknown",
                                    "ppid": int(parent_id[1]) if parent_id else None})
        except (OSError, RuntimeError):
            continue
    recovery_state["processes_with_task_working_directory"] = owned_processes[:16]
ranks = []
for rank in [0, 1]:
    lines = (work / ("ccu_rank%d.log" % rank)).read_text(errors="replace").splitlines()
    events = Counter()
    selected = []
    channel_tail = []
    for line in lines:
        if "TileXRDirectCcuTrace " not in line:
            continue
        content = line.split("TileXRDirectCcuTrace ", 1)[1]
        label = content.split("=", 1)[0].rsplit(" ", 1)[0]
        events[label] += 1
        if label in ["customChannel", "customChannel.return"]:
            channel_tail.append({"event": label, "fields": {
                key: int(value, 16 if value.startswith("0x") else 10)
                for key, value in re.findall(
                    r"\b(dieId|requestDieId|opcode|driverRet|opRet|ret|inSize|outSize)="
                    r"(-?(?:0x[0-9a-fA-F]+|\d+))\b", content)}})
        if any(key in content for key in ["endpointRoute raCtxEidInfo", "endpointRoute asyncPoll"]):
            continue
        if not ("endpointRoute " in content or "peerEndpoint " in content or
                " decoded=" in content and any(key in content for key in
                ["PfeCtx", "LocalJettyCtx", "ChannelCtxV1", "TransLocMemToRmtMem"])):
            continue
        fields = {}
        for key, value in re.findall(r"(\w+)=([^\s]+)", content):
            if key == "eidIndex" or not re.search(r"token|eid|handle|addr|va|qp$|imm$|^ctx$|^cq$|^req$", key, re.I):
                if re.fullmatch(r"-?(?:0x[0-9a-fA-F]+|\d+)", value):
                    fields[key] = int(value, 16 if value.startswith("0x") else 10)
                elif key == "decoded":
                    fields[key] = value
        if fields:
            selected.append({"event": label[:100], "fields": fields})
    ranks.append({"rank": rank, "trace_event_counts": dict(events.most_common(45)),
                  "selected_fields": selected[:35], "custom_channel_tail": channel_tail[-6:]})
plog_errors = []
first_errors = []
transport_errors = []
for path in sorted((work / "plog").rglob("*.log"))[:30]:
    for line in path.read_text(errors="replace").splitlines():
        if "[ERROR]" in line and re.search(r"HCCP|URMA|RDMA|jetty|jfc|jfs|transport", line, re.I):
            sites = re.findall(r"\[([\w./-]+\.(?:cc|cpp|c|h):\d+)\]", line)
            fields = dict(re.findall(
                r"\b(ret|retCode|errno|errCode|errorCode|status|subStatus|devId|device_id|dieId|tpn|qpId)"
                r"[=:\[]\s*(-?(?:0x[0-9a-fA-F]+|\d+))", line))
            if len(transport_errors) < 16:
                transport_errors.append({"file": path.name, "sites": sites[:2], "fields": fields})
        if "[ERROR]" in line and re.search(r"ccu|synchronize|timeout", line, re.I) and len(first_errors) < 6:
            if not re.search(r"token|password|secret|\beid\b", line, re.I):
                safe_line = re.sub(r"0x[0-9a-fA-F]{9,}", "<address omitted>", line)
                first_errors.append(safe_line[:500])
        if any(term in line for term in ["CCU Launch task error", "CCU Launch link fault", "SetCcuExceptionSqeInfo"]):
            fields = dict(re.findall(r"(status|subStatus|device_id|stream_id|task_id|retCode|instrId|missionId)=(0x[0-9a-fA-F]+|\d+)", line))
            if fields:
                plog_errors.append(fields)
header = Path("/usr/local/Ascend/cann-9.1.0/aarch64-linux/include/acl/error_codes/rt_error_codes.h")
error_definitions = [line.strip() for line in header.read_text().splitlines()
                     if re.search(r"\b(?:507046|507056)\b", line)][:4]
print("CZ_TRACE_INSPECT " + json.dumps({"inspection_only": True, "npu_workload_run": False,
      "run_id": args["run_id"], "ranks": ranks, "plog_errors": plog_errors[:15],
      "first_errors": first_errors, "transport_errors": transport_errors,
      "recovery_state": recovery_state,
      "error_definitions": error_definitions}))
