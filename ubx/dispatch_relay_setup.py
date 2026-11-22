"""Build the baseline and independently enable each of three UBX RELAY flags."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from campaign import RELAY_BRANCHES
from deploy import deploy
from dispatch_abc_setup import stage_matrix
from install_keepalive import install
from stage_sources import download, stage

REVISION = "70f1e52ad9b6f86cf3f72aaa97b7f734215704e5"
NAMESPACE = "ep32-relay-70f1e52a-c7d172ab"
MATRIX = [(variant, "dispatch", batch, mode, 2)
          for batch in (64, 128, 32) for variant in RELAY_BRANCHES for mode in ("off", "on")]


def main(args):
    build = dict(namespace=NAMESPACE, files=["build_node.sh"], program="build_node.sh",
                 interpreter="/bin/bash", argv=["dispatch-tail-relay"], unit="build")
    phase = "fetch"
    try:
        with TemporaryDirectory(prefix="tilexr-relay-") as temp, ThreadPoolExecutor(max_workers=2) as pool:
            archive, digest = download(Path(temp), {"tail-relay": {
                "commit": REVISION, "fetch_commit": True,
            }})
            phase = "upload"
            nodes = list(pool.map(lambda host: stage(host, NAMESPACE, archive, digest), args["hosts"]))
            phase = "matrix"
            list(pool.map(lambda host: stage_matrix(host, NAMESPACE, MATRIX), args["hosts"]))
            phase = "build"
            builders = list(pool.map(lambda host: deploy(host, build), args["hosts"]))
        phase = "monitor"
        install(dict(args, namespace=NAMESPACE, report_files=["tail_report.py"]))
    except Exception as error:
        print(json.dumps({"phase": phase, "error": type(error).__name__}), flush=True)
        raise SystemExit(1) from None
    print(json.dumps(dict(namespace=NAMESPACE, revision=REVISION, source_sha256=digest,
                          nodes=nodes, builds=builders, cases=len(MATRIX), batch_order=[64, 128, 32])))


if __name__ == "__main__":
    main(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
