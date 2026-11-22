"""Install the persistent all-idle gate for the Dispatch tail A/B queue."""

import json
from pathlib import Path, PurePosixPath
import shlex
import sys

from install_keepalive import install
from stage_sources import connect, run_remote


def main():
    inputs = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    namespace = "ep32-tail-ab-377fbe6d"
    cases = [(branch, "dispatch", batch, mode)
             for batch in (128, 64, 32) for mode in ("off", "on")
             for branch in ("tail-base", "tail-reviewed")]
    for host in inputs["hosts"]:
        with connect(host) as client, client.open_sftp() as transfer:
            root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / namespace
            if run_remote(client, "test -s " + shlex.quote(str(root / "sources.tar")))[0]:
                raise RuntimeError("pinned sources have not been uploaded")
            target = str(root / "matrix.json")
            with transfer.file(target + ".part", "w") as handle:
                handle.write(json.dumps(cases))
            transfer.posix_rename(target + ".part", target)
    install(dict(inputs, namespace=namespace, report_files=["tail_report.py"]))
    print(json.dumps({"namespace": namespace, "cases": len(cases), "max_tokens": 2, "warmup": 5, "measured": 20}))


if __name__ == "__main__":
    main()
