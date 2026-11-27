"""Summarize a retained Shared Jetty regression without running it again."""
import argparse
import json
from pathlib import Path
import re
import tempfile


def get_report(output):
    report = json.loads((output / "result.json").read_bytes())
    evidence = {key: report.get(key) for key in ("status", "failed_phase", "error", "elapsed_seconds")}
    evidence["phases"] = {key: value.get("status") for key, value in report["phases"].items()}
    transport = output / "run" / "transport.json"
    if transport.is_file():
        record = json.loads(transport.read_bytes())[0]
        for line in record.get("stdout", "").splitlines():
            tag, _, value = line.partition("=")
            if tag in ("SST_FAILURE", "SST_RESULT"):
                result = json.loads(value)
                evidence[tag] = {key: result.get(key) for key in (
                    "error", "runner_returncode", "remaining_processes", "simulated_us"
                )}
        patterns = ("Unknown low level", "AllToAll oracle", "Segmentation fault",
                    "Fatal:", "Mismatch", "ERROR:", "Validation failed")
        lines = record.get("stdout", "").splitlines() + record.get("stderr", "").splitlines()
        evidence["diagnostics"] = list(dict.fromkeys(
            line.strip()[:350] for line in lines if any(pattern in line for pattern in patterns)
        ))[-5:]
    return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", required=True, type=Path)
    revisions = set(json.loads(parser.parse_args().args_file.read_bytes())["revisions"])
    if not revisions or any(not re.fullmatch(r"[0-9a-f]{40}", revision) for revision in revisions):
        parser.error("supply complete commit IDs")
    records = []
    workspaces = sorted(Path(tempfile.gettempdir()).glob("urma-unitest-regression-*"),
                        key=lambda path: path.stat().st_mtime, reverse=True)
    for work in workspaces[:80]:
        marker = work / "revision.stdout.log"
        if not marker.is_file():
            continue
        revision = marker.read_text().strip()
        if revision not in revisions:
            continue
        output = work / "jetty_peers"
        row = dict(revision=revision, result_present=(output / "result.json").is_file(),
                   cleanup_receipt=(work / "cleanup.stdout.log").is_file())
        if row["result_present"]:
            row.update(get_report(output))
        records.append(row)
    print(json.dumps({"records": records}))


if __name__ == "__main__":
    main()
