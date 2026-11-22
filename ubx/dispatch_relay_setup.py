"""Build the baseline and independently enable each of three UBX RELAY flags."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from campaign import RELAY_BRANCHES, REUSE_BRANCHES
from deploy import deploy
from dispatch_abc_setup import stage_matrix
from install_keepalive import install
from stage_sources import download, stage

REVISION = "70f1e52ad9b6f86cf3f72aaa97b7f734215704e5"
NAMESPACE = "ep32-relay-70f1e52a-c7d172ab"
REUSE_REVISION = "4e2275c3003543dee60bacf071fc7ce1c717986f"
REUSE_NAMESPACE = "ep32-relay-4e2275c3-c7d172ab"


def benchmark_revision(value):
    if value == REUSE_REVISION[:8]:
        return REUSE_REVISION, REUSE_NAMESPACE, REUSE_BRANCHES
    if value == REVISION[:8]:
        return REVISION, NAMESPACE, RELAY_BRANCHES
    raise ValueError("unsupported RELAY benchmark revision")


def matrix_for(variants):
    return [(variant, "dispatch", batch, mode, 2)
            for batch in (64, 128, 32) for variant in variants for mode in ("off", "on")]


def main(args):
    revision, namespace, variants = benchmark_revision(args.get("revision", REVISION[:8]))
    matrix = matrix_for(variants)
    build = dict(namespace=namespace, files=["build_node.sh"], program="build_node.sh",
                 interpreter="/bin/bash", argv=["dispatch-tail-relay"], unit="build")
    monitor = dict(args, namespace=namespace, report_files=["tail_report.py"])
    if revision == REUSE_REVISION:
        build["argv"] = ["dispatch-tail-relay-reuse"]
        build["files"].extend(("relay_cann_compat.patch", "relay_align_shift_compat.patch"))
        monitor["start_after"] = NAMESPACE
    phase = "fetch"
    try:
        with TemporaryDirectory(prefix="tilexr-relay-") as temp, ThreadPoolExecutor(max_workers=2) as pool:
            archive, digest = download(Path(temp), {"tail-relay": {
                "commit": revision, "fetch_commit": True,
            }})
            phase = "upload"
            nodes = list(pool.map(lambda host: stage(host, namespace, archive, digest), args["hosts"]))
            phase = "matrix"
            list(pool.map(lambda host: stage_matrix(host, namespace, matrix), args["hosts"]))
            phase = "build"
            builders = list(pool.map(lambda host: deploy(host, build), args["hosts"]))
        phase = "monitor"
        install(monitor)
    except Exception as error:
        print(json.dumps({"phase": phase, "error": type(error).__name__}), flush=True)
        raise SystemExit(1) from None
    print(json.dumps(dict(namespace=namespace, revision=revision, source_sha256=digest,
                          nodes=nodes, builds=builders, cases=len(matrix), batch_order=[64, 128, 32])))


if __name__ == "__main__":
    main(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
