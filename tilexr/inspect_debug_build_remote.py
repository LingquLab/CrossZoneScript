"""Select saved build status and compiler diagnostics from the pinned candidate directory."""

import base64
import json
from pathlib import Path
import re
import sys


args = json.loads(base64.urlsafe_b64decode(sys.argv[1]))
assert re.fullmatch(r"[0-9a-f]{40}", args["revision"])
root = Path("/home/c00996840/ai") / ("TileXR-pr229-debug-" + args["revision"][:12])
record = {"directory_exists": root.is_dir(), "inspection_only": True, "logs": []}
if root.is_dir():
    revision_file = root / "REVISION"
    record["revision_recorded"] = revision_file.is_file()
    if revision_file.is_file():
        assert revision_file.read_text().strip() == args["revision"]
    saved = root / "debug-build-result.json"
    if saved.is_file():
        record["saved_result"] = json.loads(saved.read_text())
    for name in ["configure.log", "build.log", "install.log", "probe-build.log",
                 "host-check-build.log", "host-check.log"]:
        path = root / name
        if not path.is_file():
            continue
        lines = path.read_text(errors="replace").splitlines()
        selected = [line for line in lines if re.search(r"error:|fatal:|undefined reference|failed|not found", line, re.I)]
        record["logs"].append({"file": name, "bytes": path.stat().st_size,
                               "errors": [line[:600] for line in selected[-12:]]})
print("CZ_BUILD_INSPECT " + json.dumps(record))
