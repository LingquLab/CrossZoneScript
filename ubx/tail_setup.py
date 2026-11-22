"""Stage the two Dispatch revisions and start isolated AICore builds."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from deploy import deploy
from stage_sources import download, stage
from tail_queue import AB_NAMESPACE, ASSIST_NAMESPACE, start as start_queue


def main():
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    assist = args.get("assist", False)
    namespace = ASSIST_NAMESPACE if assist else AB_NAMESPACE
    branch = "codex/ubx-dispatch-tail-e4a9e4c2"
    revisions = {
        "tail-base": {"commit": "7616bf763477a54e87d38cb414b4e0dffe8bc307", "branch": branch,
                      "fetch_parent": True},
        "tail-reviewed": {"commit": "5874d0542a3136303c145c59a755a91a19553a03", "branch": branch},
    }
    build = {"namespace": namespace, "files": ["build_node.sh"],
             "program": "build_node.sh", "interpreter": "/bin/bash",
             "argv": ["dispatch-tail"], "unit": "build"}
    if assist:
        revisions = {"tail-reviewed": {"branch": branch,
                                      "commit": "e8ee4fffc2da1dfdf1acf7f67ac9a71129de868b"}}
        build["files"].append("restore_expert_copy.patch")
        build["argv"] = ["dispatch-tail-assist"]
    hosts = args["hosts"]
    if args.get("rebuild_reviewed"):
        if not assist:
            build["files"].append("restore_expert_copy.patch")
        with ThreadPoolExecutor(max_workers=2) as pool:
            states = list(pool.map(lambda host: deploy(host, build), hosts))
        print(json.dumps({"namespace": namespace, "build": states, "restored_helper": "UBXExpertCopyDirectory"}))
        return
    phase = "fetch"
    try:
        with tempfile.TemporaryDirectory(prefix="tilexr-ubx-tail-") as temp, ThreadPoolExecutor(max_workers=2) as pool:
            archive, digest = download(Path(temp), revisions)
            phase = "upload"
            destinations = list(pool.map(lambda host: stage(host, namespace, archive, digest), hosts))
            phase = "start"
            states = list(pool.map(lambda host: deploy(host, build), hosts))
        print(json.dumps({"namespace": namespace, "source_sha256": digest,
                          "nodes": destinations, "build": states}))
        if assist:
            phase = "queue"
            start_queue(args)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        result = {"phase": phase, "error": type(error).__name__}
        if isinstance(error, subprocess.CalledProcessError):
            detail = (error.stderr or "").lower()
            result["git_operation"] = error.cmd[5]
            result["git_exit"] = error.returncode
            for keyword in ("authentication", "certificate", "shallow", "resolve", "unadvertised",
                            "repository not found", "remote ref", "timeout", "proxy", "http"):
                if keyword in detail:
                    result["git_error_type"] = keyword
                    break
        print(json.dumps(result))


if __name__ == "__main__":
    main()
