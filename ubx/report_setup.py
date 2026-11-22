"""Start the independent analysis service and report current test progress."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

from deploy import deploy
from status import snapshots


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    host = args["hosts"][0]
    launch = {"namespace": args["namespace"], "files": ["report.py"],
              "program": "report.py", "interpreter": "/usr/bin/python3",
              "argv": [args["peer"]], "unit": "report"}
    if not args.get("status_only"):
        print(json.dumps({"report_service": deploy(host, launch)}))
    with ThreadPoolExecutor(max_workers=len(args["hosts"])) as pool:
        futures = [pool.submit(snapshots, node, args["namespace"]) for node in args["hosts"]]
        for future in futures:
            future.result()
