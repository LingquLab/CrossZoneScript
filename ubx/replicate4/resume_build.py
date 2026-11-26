"""Resume a configure failure caused by the missing clock archive revision marker."""
import concurrent.futures
import json
from pathlib import Path

from build import CANDIDATE, HOSTS, SSH, remote


def resume(host, script):
    output = remote(host, SSH, "root@" + host, "bash -s -- resume-dispatch",
                    payload=script, timeout=800)
    receipt = json.loads(output)
    assert receipt["built"] and receipt["revision"] == CANDIDATE
    return {"host": host, **receipt}


def main():
    script = Path(__file__).with_name("build.sh").read_bytes().replace(b"\r\n", b"\n")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda host: resume(host, script), HOSTS))
    print(json.dumps({
        "built": True, "builds": results, "resumed_at": "dispatch_configure",
        "kernel_hashes_match": (
            results[0]["artifacts"]["ubx_dispatch_ep32.o"] ==
            results[1]["artifacts"]["ubx_dispatch_ep32.o"]
        ),
        "npu_workload_started": False,
    }, separators=(",", ":")))


if __name__ == "__main__":
    main()
