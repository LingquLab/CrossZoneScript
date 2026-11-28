"""Fetch the pinned source on green and prepare its SSH-only transport."""
import io
import os
from pathlib import Path
import subprocess
import tarfile

REVISION = "0b9c3322088140c9eacb768b25edb21bda5f1161"
BRANCH = "codex/combine-ht-single-workspace"
DEPENDENCIES = ("3rdparty/spdlog", "3rdparty/picojson")


def prepare(entry, stage):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    clone = stage / "source"

    def git(label, args, timeout):
        try:
            p = subprocess.run(["git", *args], capture_output=True, env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, dict(stage=label, failure_kind="timeout")
        msg = p.stderr.decode(errors="replace").lower()
        status = dict(stage=label, exit=p.returncode)
        if p.returncode:
            status["failure_kind"] = next((term for term in (
                "authentication failed", "could not read username", "repository not found", "connect tunnel failed",
                "could not resolve", "timed out", "failed to connect", "ssl") if term in msg), "git_failure")
        return p if p.returncode == 0 else None, status

    p, status = git("green_clone", ["clone", "--depth", "1", "--branch", BRANCH,
                                  "https://github.com/LingquLab/TileXR.git", str(clone)], 60)
    if p is None:
        return None, status
    prefix = ["-C", str(clone)]
    head, status = git("green_verify", prefix + ["rev-parse", "HEAD"], 8)
    if head is None:
        return None, status
    if head.stdout.decode().strip() != REVISION:
        p, status = git("green_fetch_revision", prefix + ["fetch", "--depth", "1", "origin", REVISION], 50)
        if p is None:
            return None, status
    p, status = git("green_checkout", prefix + ["checkout", "--detach", REVISION], 12)
    if p is None:
        return None, status
    p, status = git("green_dependencies", prefix + ["submodule", "update", "--init", "--depth", "1", *DEPENDENCIES], 100)
    if p is None:
        return None, status
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=4) as output:
        def include(repo, revision, relative):
            archive, status = git("green_archive", ["-C", str(repo), "archive", revision], 25)
            if archive is None:
                raise RuntimeError(status)
            with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as source:
                for item in source:
                    if item.isdir():
                        continue
                    assert item.isfile(), item.name
                    item.name = relative + "/" + item.name
                    output.addfile(item, source.extractfile(item))

        include(clone, REVISION, "source")
        for dep in DEPENDENCIES:
            include(clone / dep, "HEAD", "source/" + dep)
        output.add(entry, arcname="remote.py")
        artifact = REVISION.encode()
        member = tarfile.TarInfo("revision.txt")
        member.size = len(artifact)
        output.addfile(member, io.BytesIO(artifact))
    return buffer.getvalue(), dict(stage="green_archive", revision=REVISION)
