"""Install the persistent all-idle gate for the Dispatch tail A/B queue."""

import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys

from install_keepalive import install
from stage_sources import connect, run_remote


AB_NAMESPACE = "ep32-tail-ab-377fbe6d"
ASSIST_NAMESPACE = "ep32-assist-e8ee4fff-377fbe6d"
ASSIST_REVISION = "e8ee4fffc2da1dfdf1acf7f67ac9a71129de868b"


def assist_identity(inputs):
    revision = inputs.get("revision", ASSIST_REVISION)
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("expected a full Git revision")
    return revision, f"ep32-assist-{revision[:8]}-377fbe6d"


def start(inputs):
    assist = inputs.get("assist", False)
    revision, namespace = assist_identity(inputs) if assist else (None, AB_NAMESPACE)
    detached = revision not in (None, ASSIST_REVISION)
    branches = ("tail-reviewed",) if assist else ("tail-base", "tail-reviewed")
    cases = [(branch, "dispatch", batch, mode)
             for batch in (128, 64, 32) for mode in ("off", "on")
             for branch in branches]
    for host in inputs["hosts"]:
        with connect(host) as client, client.open_sftp() as transfer:
            root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / namespace
            if run_remote(client, "test -s " + shlex.quote(str(root / "sources.tar")))[0]:
                raise RuntimeError("pinned sources have not been uploaded")
            target = str(root / "matrix.json")
            with transfer.file(target + ".part", "w") as handle:
                handle.write(json.dumps(cases))
            transfer.posix_rename(target + ".part", target)
            if detached and host == inputs["hosts"][0]:
                with transfer.file(str(root / "detached-rank-services"), "w"):
                    pass
    report_files = ["tail_report.py"]
    if detached:
        report_files.extend(["retry_transport.py", "retry_remaining.py"])
    install(dict(inputs, namespace=namespace, report_files=report_files))
    print(json.dumps({"namespace": namespace, "cases": len(cases), "max_tokens": 2, "warmup": 5, "measured": 20}))


if __name__ == "__main__":
    start(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
