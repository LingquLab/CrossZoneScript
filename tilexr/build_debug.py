"""Archive a pinned TileXR worktree and build a separate debug package on green 107."""

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile


def command(argv):
    return subprocess.run(argv, check=True, capture_output=True, timeout=120).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--args-file", required=True)
    args = json.loads(Path(parser.parse_args().args_file).read_text(encoding="utf-8"))
    assert args["host"] == "141.61.73.107" and args["devices"] == [2, 3]
    assert re.fullmatch(r"[0-9a-f]{40}", args["revision"])
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args["run_id"])
    code = Path(r"D:\works\code\ai\crosszone")
    repo = code / "TileXR"
    tree = code / ("TileXR-pr229-debug-" + args["revision"][:12])
    command(["git", "-C", str(repo), "fetch", "origin", args["revision"]])
    if not tree.exists():
        command(["git", "-C", str(repo), "worktree", "add", "--detach", str(tree), args["revision"]])
    assert command(["git", "-C", str(tree), "rev-parse", "HEAD"]).decode().strip() == args["revision"]
    assert not command(["git", "-C", str(tree), "status", "--porcelain"])
    archive = command(["git", "-C", str(tree), "archive", "--format=tar", args["revision"]])
    packed = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as original:
        with tarfile.open(fileobj=packed, mode="w:gz") as output:
            for member in original:
                assert member.isfile() or member.isdir(), "unsupported_source_entry"
                output.addfile(member, original.extractfile(member) if member.isfile() else None)
            for name in ["spdlog", "picojson"]:
                dependency = code / "TileXR-pr229/3rdparty" / name
                assert dependency.is_dir()
                output.add(str(dependency), arcname="3rdparty/" + name,
                           filter=lambda info: None if ".git" in Path(info.name).parts else info)
            output.add(str(Path(__file__).with_name("aiv_prepare_regression.cpp")),
                       arcname="debug_checks/aiv_prepare_regression.cpp")
    source_bytes = packed.getvalue()
    assert len(source_bytes) < 32 * 1024 * 1024, "source_archive_too_large"
    args["source_archive_sha256"] = hashlib.sha256(source_bytes).hexdigest()
    if "resume_from_run" in args:
        assert re.fullmatch(r"pr229-[A-Za-z0-9_-]{1,58}", args["resume_from_run"])
        prior = Path.cwd() / args["resume_from_run"]
        prior_request = json.loads((prior / "request.json").read_text(encoding="utf-8"))
        prior_result = json.loads((prior / "result.json").read_text(encoding="utf-8"))
        assert all(args[key] == prior_request[key] for key in
                   ["host", "devices", "revision", "rootinfo", "rootinfo_sha256", "topology_sha256"])
        failed = prior_result["results"][0]
        assert failed["status"] == "FAIL" and failed["core_build"] == "PASS"
        assert failed["error_type"] == "KeyError" and failed["reason"] == "'ASCEND_DRIVER_PATH'"
        args.update(resume_packaging=True, retained_archive_sha256=prior_request["source_archive_sha256"])
    setup = Path.cwd() / "relocate-pr229-141.61.73.107-ee779869-attempt2"
    inventory = json.loads((setup / "hosts.json").read_text(encoding="utf-8"))["clusters"]["deploy"]
    assert inventory["user"] == "root" and len(inventory["hosts"]) == 1
    assert inventory["hosts"][0]["address"] == args["host"]
    evidence = Path.cwd() / args["run_id"]
    evidence.mkdir(exist_ok=False)
    (evidence / "request.json").write_text(json.dumps(args), encoding="utf-8")
    remote = Path(__file__).with_name("build_debug_remote.py").read_text(encoding="utf-8")
    remote = remote.replace('"__CZ_SOURCE_ARCHIVE_B64__"', repr(base64.b64encode(source_bytes).decode("ascii")))
    payload = base64.urlsafe_b64encode(json.dumps(args).encode()).decode("ascii")
    script = ("set -eo pipefail\n"
              "source /home/c00996840/ai/TileXR-deploy/current/set_env.sh >/dev/null\n"
              'python3 - "$1" <<\'CZ_DEBUG_BUILD\'\n' + remote + "\nCZ_DEBUG_BUILD\n")
    argv = [os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"), "/d", "/c",
            str(repo / "tools/tssh/tssh.cmd"), "-c", str(setup / "hosts.json"), "-C", "deploy",
            "--ssh", str(setup / "ssh.cmd"), "--connect-timeout", "10", "--timeout", "900", "-I",
            "--", "timeout", "--signal=TERM", "--kill-after=10s", "840s", "bash", "-s", "--", payload]
    result = subprocess.run(argv, input=script.encode(), capture_output=True, timeout=930)
    (evidence / "stdout.log").write_bytes(result.stdout)
    (evidence / "stderr.log").write_bytes(result.stderr)
    records = [json.loads(line[len("CZ_DEBUG_BUILD "):])
               for line in result.stdout.decode("utf-8", errors="replace").splitlines()
               if line.startswith("CZ_DEBUG_BUILD ")]
    summary = {"host": args["host"], "tssh_exit_code": result.returncode,
               "results": records, "evidence_directory": str(evidence)}
    (evidence / "result.json").write_text(json.dumps(summary), encoding="utf-8")
    assert result.returncode == 0 and len(records) == 1, "inspect_saved_debug_build"
    assert records[0]["status"] == "PASS"
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
