"""Fetch TileXR in green, upload a pinned source archive, then run on the NPU host."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import tempfile

ORIGIN = "https://github.com/LingquLab/TileXR.git"
DEPENDENCIES = ("3rdparty/picojson", "3rdparty/spdlog")
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
       "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4"]


class StageFailure(Exception):
    pass


def capture(argv, *, timeout, **kwargs):
    return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout, **kwargs)


def checked(stage, argv, *, timeout, **kwargs):
    print(json.dumps({"stage": stage}), flush=True)
    run = capture(argv, timeout=timeout, **kwargs)
    if run.returncode:
        output = run.stdout + b"\n" + run.stderr
        diagnostics = {
            "tls": rb"certificate (problem|verify failed)",
            "auth": rb"could not read username|authentication failed|permission denied",
            "not_found": rb"not found|no such file|couldn't find remote ref",
            "connect": rb"could not resolve|connection timed out|connection refused",
            "remote_transfer": rb"early eof|unexpected disconnect|rpc failed",
            "disk": rb"no space left on device",
        }
        matched = [name for name, pattern in diagnostics.items() if re.search(pattern, output, re.I)]
        print(json.dumps({"stage": stage, "rc": run.returncode, "diagnostics": matched,
                          "stdout_bytes": len(run.stdout), "stderr_bytes": len(run.stderr)}))
        raise StageFailure from None
    return run.stdout


def bundle(args, staging):
    repo = staging / "TileXR"
    checked("green_git_init", ["git", "init", "-q", str(repo)], timeout=15)
    git = ["git", "-C", str(repo)]
    checked("green_git_origin", git + ["remote", "add", "origin", ORIGIN], timeout=15)
    checked("green_git_fetch", git + ["fetch", "--no-tags", "--depth=1", "origin", args.ref], timeout=240)
    head = checked("green_git_revision", git + ["rev-parse", "FETCH_HEAD"], timeout=15).decode().strip()
    if head != args.revision:
        print(json.dumps({"requested": args.revision, "fetched": head}))
        raise SystemExit(1)
    checked("green_git_checkout", git + ["-c", "core.autocrlf=false", "checkout", "--detach", head], timeout=90)
    checked("green_git_dependencies",
            git + ["submodule", "update", "--init", "--depth", "1", "--", *DEPENDENCIES], timeout=240)

    archive = staging / "tilexr.tar.gz"
    with tarfile.open(archive, "w:gz", compresslevel=3) as out:
        for relative in ("", *DEPENDENCIES):
            source = repo / relative
            tar = staging / "part.tar"
            command = ["git", "-C", str(source), "archive", "--output", str(tar)]
            if relative:
                command += ["--prefix", relative + "/"]
            checked("green_git_archive", command + ["HEAD"], timeout=90)
            with tarfile.open(tar) as part:
                for entry in part:
                    out.addfile(entry, part.extractfile(entry) if entry.isfile() else None)
    return archive


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="public_fp8")
    p.add_argument("--revision", default="2b00ab8decae75b3a93a9a8471762e107205189a")
    p.add_argument("--ref", default="refs/heads/codex/combine-ht-multi-jetty")
    p.add_argument("--reuse-build", action="store_true")
    p.add_argument("--idle-timeout", type=int, default=2700)
    p.add_argument("--args-file", type=Path)
    args = p.parse_args()
    if args.args_file is not None:
        for key, value in json.loads(args.args_file.read_text(encoding="utf-8")).items():
            expected = {"reuse_build": bool, "idle_timeout": int}.get(key, str)
            if key not in ("host", "revision", "ref", "reuse_build", "idle_timeout") or not isinstance(value, expected):
                p.error(f"invalid script argument {key!r}")
            setattr(args, key, value)
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        p.error("--revision must be a full SHA")
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    ssh = SSH + [args.host]
    root = '"$HOME/.cache/tilexr-ht-be110cc8"'
    remote_archive = '"$HOME/.cache/tilexr-ht-be110cc8/tilexr-' + args.revision + '.tar.gz"'
    digest = "reuse"
    if not args.reuse_build:
        with tempfile.TemporaryDirectory() as temporary:
            archive = bundle(args, Path(temporary))
            with archive.open("rb") as data:
                sha = hashlib.sha256()
                for block in iter(lambda: data.read(1024 * 1024), b""):
                    sha.update(block)
                digest = sha.hexdigest()
                data.seek(0)
                upload = f'umask 077 && mkdir -p {root} && cat > {remote_archive}.part && mv {remote_archive}.part {remote_archive}'
                checked("upload", ssh + [upload], stdin=data, timeout=180)
    shell = Path(__file__).with_name("smoke.sh").read_bytes().replace(b"\r\n", b"\n")
    command = "bash -s -- " + shlex.join((args.revision, digest, str(args.idle_timeout)))
    result = capture(ssh + [command], input=shell, timeout=2 * args.idle_timeout + 850)
    ssh_errors = ("connection reset", "broken pipe", "connection closed", "timed out", "connection refused")
    print(json.dumps({"stage": "remote_attempt", "exit": result.returncode,
                      "stderr_bytes": len(result.stderr), "ssh_errors": [
                          item for item in ssh_errors if item.encode() in result.stderr.lower()]}))
    allowed = ("revision=", "cann=", "build=", "step=", "diagnostic=", "devices_idle=", "mode=")
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        if line.startswith(allowed):
            print(line[:220])
    # This operation reports a completed attempt. The remote status above carries smoke success.
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except StageFailure:
        pass
    except (OSError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"observation": "runner_error", "type": type(error).__name__,
                          "errno": getattr(error, "errno", None)}))
