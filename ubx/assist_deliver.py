"""Download the final six-case report and complete packet captures."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import uuid

from export_assist_profiles import export
from tail_results import collect
from tail_queue import assist_identity


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    hosts = args["hosts"]
    revision, _ = assist_identity(args)
    report = collect({"host": hosts[0], "assist": True, "pack": True, "revision": revision})
    if not report.get("green_windows_bundle") or len(report["results"]) != 6:
        raise RuntimeError("all six verified cases are required for delivery")
    print(json.dumps(report, separators=(",", ":")), flush=True)
    stamp = "complete-" + uuid.uuid4().hex[:8]
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(export, host, stamp, False, revision) for host in hosts]
        for job in jobs:
            data = job.result()
            fields = ("host", "green_windows_bundle", "size", "sha256", "cases")
            print(json.dumps({key: data[key] for key in fields}, separators=(",", ":")), flush=True)
