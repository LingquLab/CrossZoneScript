"""Select numeric route, context and failure fields from an existing two-rank run."""

import base64
from collections import Counter
import json
from pathlib import Path
import re
import sys


args = json.loads(base64.urlsafe_b64decode(sys.argv[1]))
assert re.fullmatch(r"pr229-[A-Za-z0-9_-]{1,58}", args["run_id"])
parent = Path("/home/c00996840/ai/TileXR-test").resolve(strict=True)
work = (parent / args["run_id"]).resolve(strict=True)
assert work.parent == parent
result = json.loads((work / "result.json").read_text())
assert result["devices"] == [2, 3]
ranks = []
for rank in [0, 1]:
    lines = (work / ("ccu_rank%d.log" % rank)).read_text(errors="replace").splitlines()
    events = Counter()
    selected = []
    for line in lines:
        if "TileXRDirectCcuTrace " not in line:
            continue
        content = line.split("TileXRDirectCcuTrace ", 1)[1]
        label = content.split("=", 1)[0].rsplit(" ", 1)[0]
        events[label] += 1
        if not ("endpointRoute " in content or "peerEndpoint " in content or
                " decoded=" in content and any(key in content for key in
                ["PfeCtx", "LocalJettyCtx", "ChannelCtxV1", "TransLocMemToRmtMem"])):
            continue
        fields = {}
        for key, value in re.findall(r"(\w+)=([^\s]+)", content):
            if key == "eidIndex" or not re.search(r"token|eid|handle|addr|va|qp$|imm$", key, re.I):
                if re.fullmatch(r"-?(?:0x[0-9a-fA-F]+|\d+)", value):
                    fields[key] = int(value, 16 if value.startswith("0x") else 10)
                elif key == "decoded":
                    fields[key] = value
        if fields:
            selected.append({"event": label[:100], "fields": fields})
    ranks.append({"rank": rank, "trace_event_counts": dict(events.most_common(45)),
                  "selected_fields": selected[:35]})
plog_errors = []
for path in sorted((work / "plog").rglob("*.log"))[:30]:
    for line in path.read_text(errors="replace").splitlines():
        if any(term in line for term in ["CCU Launch task error", "CCU Launch link fault", "SetCcuExceptionSqeInfo"]):
            fields = dict(re.findall(r"(status|subStatus|device_id|stream_id|task_id|retCode|instrId|missionId)=(0x[0-9a-fA-F]+|\d+)", line))
            if fields:
                plog_errors.append(fields)
print("CZ_TRACE_INSPECT " + json.dumps({"inspection_only": True, "npu_workload_run": False,
      "run_id": args["run_id"], "ranks": ranks, "plog_errors": plog_errors[:15]}))
