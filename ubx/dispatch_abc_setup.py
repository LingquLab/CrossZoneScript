"""Stage one pinned TileXR revision and install its EP32 TopK A/B/C campaign."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path, PurePosixPath
import sys
from tempfile import TemporaryDirectory

from campaign import ABC_BRANCHES
from deploy import deploy
from install_keepalive import install
from stage_sources import connect, download, stage

REVISION = "fe5e617d54f5735fb08c730aa71e71a76c5e6988"
BRANCH = "codex/ubx-dispatch-tail-e4a9e4c2"
NAMESPACE = "ep32-topk-abc-fe5e617d-c7d172ab"
MATRIX = [(variant, "dispatch", batch, mode, 2)
          for batch in (64, 128, 32) for variant in ABC_BRANCHES for mode in ("off", "on")]


def stage_matrix(host):
    with connect(host) as client, client.open_sftp() as transfer:
        transfer.get_channel().settimeout(30)
        root = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / NAMESPACE
        target = str(root / "matrix.json")
        with transfer.file(target + ".part", "w") as output:
            output.write(json.dumps(MATRIX))
        transfer.posix_rename(target + ".part", target)


def main(args):
    revisions = {label: {"commit": REVISION, "branch": BRANCH} for label in ABC_BRANCHES}
    build = dict(namespace=NAMESPACE, files=["build_node.sh"], program="build_node.sh",
                 interpreter="/bin/bash", argv=["dispatch-tail-abc"], unit="build")
    phase = "fetch"
    try:
        with TemporaryDirectory(prefix="tilexr-topk-abc-") as temp, ThreadPoolExecutor(max_workers=2) as pool:
            archive, digest = download(Path(temp), revisions)
            phase = "upload"
            nodes = list(pool.map(lambda host: stage(host, NAMESPACE, archive, digest), args["hosts"]))
            phase = "matrix"
            list(pool.map(stage_matrix, args["hosts"]))
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
