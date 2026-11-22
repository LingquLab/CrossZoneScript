"""Stage the two Dispatch revisions and start isolated AICore builds."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile

from deploy import deploy
from stage_sources import download, stage


def main():
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    namespace = "ep32-tail-ab-377fbe6d"
    branch = "codex/ubx-dispatch-tail-e4a9e4c2"
    revisions = {
        "tail-base": {"commit": "7616bf763477a54e87d38cb414b4e0dffe8bc307", "fetch_commit": True},
        "tail-reviewed": {"commit": "5874d0542a3136303c145c59a755a91a19553a03", "branch": branch,
                          "fetch_commit": True},
    }
    build = {"namespace": namespace, "files": ["build_node.sh"],
             "program": "build_node.sh", "interpreter": "/bin/bash",
             "argv": ["dispatch-tail"], "unit": "build"}
    hosts = args["hosts"]
    with tempfile.TemporaryDirectory(prefix="tilexr-ubx-tail-") as temp, ThreadPoolExecutor(max_workers=2) as pool:
        archive, digest = download(Path(temp), revisions)
        destinations = list(pool.map(lambda host: stage(host, namespace, archive, digest), hosts))
        states = list(pool.map(lambda host: deploy(host, build), hosts))
    print(json.dumps({"namespace": namespace, "source_sha256": digest,
                      "nodes": destinations, "build": states}))


if __name__ == "__main__":
    main()
