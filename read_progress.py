from pathlib import Path
import json
import sys

from run_matrix import RESULT, STATE


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        return None


def host_receipt(host):
    brief = {key: host[key] for key in ("node", "status", "reason",
        "hardware_launched", "performance_valid", "rank_rounds") if key in host}
    error = host.get("error", "")
    for message in ("Prepared request changed: profile", "Prepared request changed: port",
                    "Prepared candidate changed", "Prepared package changed",
                    "Configured rendezvous port range is occupied", "A local rank failed",
                    "Case deadline exceeded", "Foreign workload detected",
                    "Peer benchmark worker failed", "Summary contract mismatch", "Packet limit or capture mode mismatch"):
        if message in error:
            brief["error_detail"] = message
            break
    if error:
        brief["error_type"] = error.split(":", 1)[0]
    if host.get("hardware_launched"):
        brief["network_restored"] = host.get("network_restored")
        brief["exited_ranks"] = sum(isinstance(code, int) for code in host.get("exit_codes", {}).values())
    offender = host.get("interference") or {}
    if offender.get("kind") in ("current_fixture", "other_fixture", "profiler", "python", "shell", "other", "unavailable"):
        brief["interference"] = {key: offender[key] for key in ("device", "kind", "own_session", "own_parent")
                                 if key in offender}
    rows = host.get("before_occupancy", host.get("occupancy", []))
    if rows:
        brief["busy"] = [row["device"] for row in rows if row.get("pids")]
        brief["unusable"] = [row["device"] for row in rows if not row.get("usable")]
    return brief


def snapshots():
    latest = []
    for node in (0, 1):
        files = sorted((RESULT / "occupancy").rglob(f"*-{node}.stdout"),
                       key=lambda path: path.stat().st_mtime, reverse=True)
        for path in files:
            sample = load(path)
            if not sample or not sample.get("rows"):
                continue
            rows = sample["rows"]
            latest.append({
                "node": node,
                "usable": sum(bool(row.get("usable")) for row in rows),
                "busy": [row["device"] for row in rows if row.get("pids")],
                "sample_time": round(path.stat().st_mtime),
            })
            break
    return latest


def main(args):
    summaries = []
    for directory in sorted(RESULT.glob("bs*-*")):
        if not directory.is_dir():
            continue
        case = {"case": directory.name}
        for name in ("prepare", "run", "collect"):
            receipt = load(directory / (name + ".private.json"))
            if receipt:
                case[name] = [host_receipt(host) for host in receipt]
            elif name == "run":
                pending = [load(directory / f"run-{node}.stdout") for node in (0, 1)]
                case["pending_run"] = [host_receipt(host) for host in pending if host]
        summary = load(directory / "summary.json")
        if summary:
            case["summary"] = summary
            identity = load(directory / "run-identity.json")
            archives = load(directory / "collect.private.json")
            if identity:
                case["run_id"] = identity["run_id"]
            if archives:
                case["archives"] = [
                    {"node": node, "bytes": row["capture_archive_bytes"], "sha256": row["capture_archive_sha256"],
                     "present": (directory / f"node-{node}-captures.tar.gz").is_file()}
                    for node, row in enumerate(archives)
                ]
        summaries.append(case)
    status = None
    for path in sorted((RESULT / "occupancy").glob("*/status.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True):
        status = load(path)
        if status:
            break
    exit_rows = []
    if "iteration" in args:
        iteration = int(args["iteration"])
        for path in sorted((STATE / "runs/ubx-topk-perf-matrix" / str(iteration)).rglob("stdout.log"))[:8]:
            for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                exit_rows.append({key: row[key] for key in ("status", "reason", "error_type", "bs",
                    "package", "nodes", "step", "hosts", "function", "line") if key in row})
    attempts = []
    for directory in sorted((RESULT / "previous-attempts").glob("bs*-*"),
                            key=lambda path: path.name.rsplit("-", 1)[1], reverse=True)[:4]:
        receipt = load(directory / "run.private.json")
        if receipt:
            attempts.append({"case": directory.name.rsplit("-", 1)[0],
                             "hosts": [host_receipt(host) for host in receipt]})
    print(json.dumps({"cases": summaries, "status": status, "latest_occupancy": snapshots(),
                      "matrix_results": exit_rows, "recent_attempts": attempts}, separators=(",", ":")))


if __name__ == "__main__":
    assert sys.argv[1] == "--args-file"
    main(load(Path(sys.argv[2])))
