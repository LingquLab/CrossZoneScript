"""Fetch immutable Git trees on the bridge and stage them on the test nodes."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile

import paramiko

REPO = "https://github.com/LingquLab/TileXR.git"
MODULES = {
    "picojson": "https://github.com/kazuho/picojson.git",
    "spdlog": "https://github.com/gabime/spdlog.git",
    "tilelang-ascend": "https://github.com/tile-ai/tilelang-ascend.git",
}
logging.getLogger("paramiko").addHandler(logging.NullHandler())
logging.getLogger("paramiko").propagate = False


def git(path, *args):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    return subprocess.run(
        ["git", "-C", str(path), "-c", "credential.interactive=never", *args],
        env=env, check=True, capture_output=True, text=True, timeout=240,
    ).stdout.strip()


def download(root, revisions):
    source = root / "tilexr.git"
    source.mkdir()
    git(source, "init", "--bare", "--quiet")
    modules = {}
    for label, item in revisions.items():
        ref = "refs/heads/" + item["branch"]
        commit = item["commit"]
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("invalid pinned commit")
        git(source, "fetch", "--quiet", "--no-tags", "--depth=1", REPO, ref + ":refs/heads/" + label)
        if git(source, "rev-parse", "refs/heads/" + label) != commit:
            raise ValueError("branch changed after pinning")
        for name in MODULES:
            module = git(source, "ls-tree", commit, "3rdparty/" + name).split()
            if len(module) != 4 or module[:2] != ["160000", "commit"]:
                raise ValueError("unexpected module layout")
            expected = modules.setdefault(name, module[2])
            if module[2] != expected:
                raise ValueError("module pins differ across sources")
    for name, commit in modules.items():
        module = root / (name + ".git")
        module.mkdir()
        git(module, "init", "--bare", "--quiet")
        git(module, "fetch", "--quiet", "--no-tags", "--depth=1", MODULES[name],
            commit + ":refs/heads/pinned")
        if git(module, "rev-parse", "refs/heads/pinned") != commit:
            raise ValueError("incorrect module revision")
    manifest = {"revisions": revisions, "modules": modules}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    archive = root / "sources.tar"
    with tarfile.open(archive, "w") as tar:
        for entry in sorted(root.iterdir()):
            if entry != archive:
                tar.add(entry, arcname=entry.name)
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return archive, digest


def credentials(host):
    if not re.fullmatch(r"[\w.:-]+", host):
        raise ValueError("invalid host")
    program = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$dir = Join-Path $env:LOCALAPPDATA 'CrossZone\ssh-credentials'
$c = Import-Clixml -LiteralPath (Join-Path $dir '__HOST__.xml')
[Console]::Write((ConvertTo-Json -InputObject @($c.UserName, $c.GetNetworkCredential().Password) -Compress))
""".replace("__HOST__", host)
    run = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", program],
        check=True, capture_output=True, timeout=8,
    )
    return json.loads(run.stdout.decode("utf-8-sig"))


def connect(host):
    user, password = credentials(host)
    client = paramiko.SSHClient()
    client.load_host_keys(str(Path.home() / ".ssh" / "known_hosts"))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(host, username=user, password=password, allow_agent=False,
                   look_for_keys=False, timeout=7, banner_timeout=7, auth_timeout=10)
    return client


def run_remote(client, command, timeout=30, input_text=None):
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if input_text is not None:
        stdin.write(input_text)
        stdin.flush()
    stdin.channel.shutdown_write()
    output = stdout.read(8192).decode("utf-8", "replace").strip()
    errors = stderr.read(8192).decode("utf-8", "replace").strip()
    return stdout.channel.recv_exit_status(), output, errors


def stage(host, namespace, archive, digest):
    with connect(host) as client:
        with client.open_sftp() as transfer:
            transfer.get_channel().settimeout(60)
            destination = str(PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx" / namespace)
            target = shlex.quote(destination)
            command = "umask 077; mkdir -p -- " + target
            if run_remote(client, command)[0] != 0:
                raise RuntimeError("cannot create campaign directory")
            temporary = destination + "/sources.tar.part"
            transfer.put(str(archive), temporary, confirm=True)
            command = (
                'set -eu; cd ' + target + '; '
                + "printf '%s  %s\\n' " + shlex.quote(digest) + " sources.tar.part | sha256sum -c >/dev/null; "
                + "mv -f -- sources.tar.part sources.tar"
            )
            if run_remote(client, command, timeout=90)[0] != 0:
                raise RuntimeError("remote archive verification failed")
    return destination


def main():
    if len(sys.argv) != 3 or sys.argv[1] != "--args-file":
        raise SystemExit("expected --args-file")
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    namespace = args["namespace"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", namespace):
        raise ValueError("invalid namespace")
    try:
        with tempfile.TemporaryDirectory(prefix="tilexr-ubx-source-") as temporary:
            archive, digest = download(Path(temporary), args["revisions"])
            with ThreadPoolExecutor(max_workers=len(args["hosts"])) as pool:
                destinations = list(pool.map(
                    lambda host: stage(host, namespace, archive, digest), args["hosts"]
                ))
            print(json.dumps({
                "sha256": digest, "bytes": archive.stat().st_size,
                "destinations": dict(zip(args["hosts"], destinations)),
            }))
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or "").lower() if isinstance(exc.stderr, str) else ""
        outcome = "git_tls" if "certificate" in details else (
            "git_auth" if "authentication" in details or "could not read username" in details
            else "subprocess_failed"
        )
        print(json.dumps({"outcome": outcome, "exit_code": exc.returncode}))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, paramiko.SSHException) as exc:
        print(json.dumps({"outcome": type(exc).__name__}))


if __name__ == "__main__":
    main()
