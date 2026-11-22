"""Stage the two Dispatch revisions and start isolated AICore builds."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from deploy import deploy
from stage_sources import download, stage


def main():
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    namespace = "ep32-tail-ab-377fbe6d"
    branch = "codex/ubx-dispatch-tail-e4a9e4c2"
    revisions = {
        "tail-base": {"commit": "7616bf763477a54e87d38cb414b4e0dffe8bc307", "branch": branch,
                      "fetch_parent": True},
        "tail-reviewed": {"commit": "5874d0542a3136303c145c59a755a91a19553a03", "branch": branch},
    }
    build = {"namespace": namespace, "files": ["build_node.sh"],
             "program": "build_node.sh", "interpreter": "/bin/bash",
             "argv": ["dispatch-tail"], "unit": "build"}
    hosts = args["hosts"]
    if args.get("rebuild_reviewed"):
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
