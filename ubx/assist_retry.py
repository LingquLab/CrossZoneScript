"""Inspect the interrupted BS128 capture and queue one transport recovery."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

from deploy import deploy
from read_attempt import inspect
from tail_queue import ASSIST_NAMESPACE


def main():
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    case = "tail-reviewed-dispatch-bs128-on"
    args.update(namespace=ASSIST_NAMESPACE, case=case)
    hosts = args["hosts"]
    if args.get("host_barrier"):
        config = {"namespace": ASSIST_NAMESPACE,
                  "files": ["retry_host_barrier.py", "retry_remaining.py", "retry_transport.py"],
                  "program": "retry_host_barrier.py", "interpreter": "/usr/bin/python3",
                  "unit": "retry-host-barrier", "argv": [hosts[0], args["peer"]]}
        print(json.dumps({"retry": deploy(hosts[0], config)}, separators=(",", ":")))
        return
    if args.get("remaining"):
        config = {"namespace": ASSIST_NAMESPACE, "files": ["retry_remaining.py", "retry_transport.py"],
                  "program": "retry_remaining.py", "interpreter": "/usr/bin/python3",
                  "unit": "retry-remaining", "argv": [hosts[0], args["peer"]]}
        print(json.dumps({"remaining": deploy(hosts[0], config)}, separators=(",", ":")))
        return
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(inspect, host, args, i == 0) for i, host in enumerate(hosts)]
        observations = [future.result() for future in futures]
    for evidence in observations:
        print(json.dumps(evidence, separators=(",", ":")))
    failure = observations[0]["cases"].get(case, {})
    if "255" not in failure.get("reason", ""):
        return
    config = {"namespace": ASSIST_NAMESPACE, "files": ["retry_transport.py"],
              "program": "retry_transport.py", "interpreter": "/usr/bin/python3",
              "unit": "read-retry", "argv": [hosts[0], args["peer"], case]}
    print(json.dumps({"retry": deploy(hosts[0], config)}, separators=(",", ":")))


if __name__ == "__main__":
    main()
