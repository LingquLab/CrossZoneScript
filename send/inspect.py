# Copyright (c) 2026 TileXR Project.
"""Read retained combine HT evidence without starting or changing an SST run."""
import json
from pathlib import Path
import socket
import sys


def summary(value):
    keys = ("returncode", "send_verified", "verified_ranks", "checked_wqes", "mismatch_count",
            "failure", "fault_address", "pc", "link", "constructed", "first_fatal", "first_errors")
    if isinstance(value, dict):
        out = {k: value[k] for k in keys if k in value}
        if "first_mismatches" in value:
            out["first_mismatch"] = value["first_mismatches"][:1]
        if "ranks" in value and isinstance(value["ranks"], list):
            ranks = value["ranks"]
            out["matching_ranks"] = sum(bool(x.get("match")) for x in ranks)
            out["rank_count"] = len(ranks)
            out["first_rank"] = ranks[:1]
        return out
    if isinstance(value, list):
        return [summary(x) for x in value[:3]]
    return str(value)[:80]


def main(args):
    assert socket.gethostname().split(".")[0] == args["hostname"]
    root = Path(args["prior"])
    assert root.parent == Path("/var/tmp") and root.name.startswith("tilexr-combine-ht-")
    progress = json.loads((root / "progress.json").read_text())
    case = root / Path(progress["case"]).relative_to("/work")
    files = sorted((f for f in case.glob("*/*.json") if f.name in ("analysis.json", "trial.json", "diagnosis.json")),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    if args.get("prefixes"):
        files = [f for f in files if f.parent.name.startswith(tuple(args["prefixes"]))]
    offset = args.get("offset", 0)
    rows = []
    for f in files[offset:offset + 4]:
        result = summary(json.loads(f.read_text()))
        text = json.dumps(result, separators=(",", ":"))
        rows.append({"file": str(f.relative_to(case)), "summary": result if len(text) < 330 else text[:320]})
    print(json.dumps({"case": case.name, "report_count": len(files), "offset": offset, "reports": rows},
                     separators=(",", ":")))


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
