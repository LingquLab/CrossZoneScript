"""Stage and build the audit fixture in green, then run when this device pair is available."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
from tempfile import TemporaryDirectory

from stage_sources import connect, download, run_remote, stage


def prepare(host, namespace, original, revision, archive, digest):
    path = Path(__file__).parent
    root = stage(host, namespace, archive, digest)
    with connect(host) as client, client.open_sftp() as sftp:
        sftp.get_channel().settimeout(60)
        for file in ("build_combine_audit.sh", "run_combine_audit.py"):
            sftp.put(str(path / file), root + "/" + file, confirm=True)
        original_dir = str(PurePosixPath(root).with_name(original))
        command = shlex.join(["/bin/bash", root + "/build_combine_audit.sh", root, original_dir, revision])
        code, _, _ = run_remote(client, command, timeout=480)
        if code:
            _, details, _ = run_remote(client, "tail -n 15 " + shlex.quote(root + "/build.log"), timeout=10)
            issues = [line[:210] for line in details.splitlines() if "error" in line.lower()][:5]
            return {"path": root, "compile_ok": False, "issues": issues}
        return {"path": root, "compile_ok": True}


def main():
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    namespace, original, hosts = args["namespace"], args["original"], args["hosts"]
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]+", value) for value in (namespace, original)):
        raise ValueError("invalid campaign namespace")
    revision = args["candidate"]["commit"]
    with TemporaryDirectory(prefix="tilexr-combine-audit-") as directory:
        archive, digest = download(Path(directory), {"audit": args["candidate"]})
        with ThreadPoolExecutor(max_workers=2) as pool:
            builds = list(pool.map(lambda host: prepare(host, namespace, original, revision, archive, digest), hosts))
    print(json.dumps({"builds": dict(zip(hosts, builds))}))
    if all(result["compile_ok"] for result in builds):
        with connect(hosts[0]) as client:
            command = shlex.join(["/usr/bin/python3", builds[0]["path"] + "/run_combine_audit.py",
                                  "audit", original, hosts[0], args["peer"]])
            code, output, _ = run_remote(client, command, timeout=250)
            print(output if code == 0 else json.dumps({"phase": "audit_dispatch_failed", "exit": code}))


if __name__ == "__main__":
    main()
