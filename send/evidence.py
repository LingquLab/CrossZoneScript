# Copyright (c) 2026 TileXR Project.
"""Read retained combine HT evidence without starting or changing an SST run."""
import json
from pathlib import Path
import socket
import subprocess
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
    if args["action"] == "boundary-result":
        print(json.dumps(json.loads((Path(__file__).resolve().parent / "boundary-result.json").read_text()), separators=(",", ":")))
        return
    if args["action"] in ("trial-result", "outline"):
        work = Path(__file__).resolve().parent
        file = work / "trial-result.json"
        if not file.exists():
            print(json.dumps({"result_exists": False}))
            return
        value = json.loads(file.read_text())
        if args["action"] == "outline":
            folder = work / Path(value["report"]).relative_to("/work")
            p = subprocess.run(["readelf", "-sW", str(folder / "send.o")], capture_output=True, text=True, timeout=15)
            assert p.returncode == 0
            funcs = [line.split()[-1] for line in p.stdout.splitlines() if " FUNC " in line]
            names = [name[:200] for name in funcs if any(x in name for x in ("BuildPut", "LocalUrma", "LocalPut", "Simulator", "PutAddress"))]
            print(json.dumps({"function_count": len(funcs), "posting_functions": names[:8]}, separators=(",", ":")))
            return
        out = {k: value[k] for k in ("ok", "variant", "build_completed", "send_verified", "verified_ranks",
               "baseline_object_sha256", "object_sha256", "route_sha256", "send_rows", "error", "failure",
               "force_inline", "outlined_builders", "stop_boundary", "boundary_verified",
               "checked_status_words", "nonzero_status_words", "status_mismatch_count", "status_encoding",
               "mte_status", "untouched_snapshots") if k in value}
        out["stages"] = [{k: s[k] for k in ("returncode", "wall_s", "completed", "simulated_us", "failure") if k in s}
                         for s in value.get("stages", [])]
        folder = work / Path(value["report"]).relative_to("/work")
        build_log = folder / "build.log"
        if build_log.exists() and not value.get("build_completed"):
            out["compile_errors"] = [s[:250] for s in build_log.read_text(errors="replace").splitlines() if "error:" in s][:2]
        sim_log = folder / "send-only/sim.log"
        if sim_log.exists() and not value.get("ok"):
            with sim_log.open("rb") as f:
                f.seek(max(0, sim_log.stat().st_size - 20000))
                lines = f.read().decode(errors="replace").splitlines()
            out["sim_errors"] = [s[max(0, s.lower().find("fatal:")):][:450] for s in lines
                                 if any(x in s.lower() for x in ("fatal:", "malloc", "corrupt", "assertion"))][:2]
        for k in ("diagnostic_only", "wqe_verified", "checked_wqes", "checked_wqe_fields", "mismatch_count", "first_mismatch", "status_words", "returncode", "wall_s"):
            if k in value:
                out[k] = value[k]
        capture_log = folder / "capture/sim.log"
        if capture_log.exists():
            with capture_log.open("rb") as f:
                f.seek(max(0, capture_log.stat().st_size - 20000))
                lines = f.read().decode(errors="replace").splitlines()
            out["capture_errors"] = [s[max(0, s.lower().find("fatal:")):][:450] for s in lines
                                     if any(x in s.lower() for x in ("fatal:", "malloc", "corrupt", "assertion"))][:2]
        print(json.dumps(out, separators=(",", ":")))
        return
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
