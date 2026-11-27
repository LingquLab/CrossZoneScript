"""Fetch the pinned source in the green workspace and build it on both hosts."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

HOSTS = ("90.90.93.188", "90.90.93.84")
SSH = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/OpenSSH/ssh.exe"
SSH_OPTIONS = ["-o", "ConnectTimeout=12", "-o", "BatchMode=yes",
               "-o", "StrictHostKeyChecking=yes"]
ROOT = "/root/ubx-replicate4-e172cd2-bs32-f50"
TILEXR = "https://github.com/LingquLab/TileXR.git"
CANDIDATE = "e172cd211889e6da70e35da315771508a1a92c47"
RUNTIME = "0797410beab1da40800611affc9c512cd42525ba"
CLOCK = "3c7195b5fc8bc1584fcb05f891bc32d07e359dff"


def git(repo, *args, timeout=120):
    executable = shutil.which("git")
    if not executable:
        raise RuntimeError("Git is unavailable in the green workspace")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(
        [executable, "-C", str(repo), *args], env=env, capture_output=True, timeout=timeout,
    )
    if result.returncode:
        detail = result.stderr.decode(errors="replace").lower()
        reason = next((label for label, needles in (
            ("remote-ref-unavailable", ("couldn't find remote ref", "not our ref", "unadvertised object")),
            ("authentication-failed", ("authentication", "could not read username", "repository not found")),
            ("network-unreachable", ("could not resolve", "failed to connect", "timed out", "connection reset")),
            ("transport-failed", ("rpc failed", "early eof", "disconnect", "schannel", "ssl")),
            ("filesystem-failed", ("filename too long", "permission denied", "could not create")),
        ) if any(needle in detail for needle in needles)), "unclassified")
        raise RuntimeError(f"Green Git {args[0]} exited {result.returncode}: {reason}")
    return result.stdout.decode().strip()


def pin(repo, origin, revisions, source_ref=None):
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "-c", "http.https://github.com/.sslVerify=false", "fetch", "--no-tags",
        "--depth=1", origin, *(revisions if source_ref is None else (source_ref,)), timeout=240)
    for revision in revisions:
        assert git(repo, "rev-parse", f"{revision}^{{commit}}") == revision


def archive(work, repo, name, revision, *paths):
    output = work / f"{name}.tar.gz"
    git(repo, "archive", "--format=tar.gz", "--output", str(output), revision, *paths)
    return output


def prepare(work):
    tilexr = work / "tilexr-git"
    pin(tilexr, TILEXR, (CANDIDATE, RUNTIME, CLOCK))
    archives = [
        archive(work, tilexr, "source", CANDIDATE, "dispatch/dispatch-release"),
        archive(work, tilexr, "runtime-source", RUNTIME),
        archive(work, tilexr, "clock-source", CLOCK),
    ]
    for name, origin in (
        ("spdlog", "https://github.com/gabime/spdlog.git"),
        ("picojson", "https://github.com/kazuho/picojson.git"),
    ):
        path = f"3rdparty/{name}"
        gitlink = git(tilexr, "ls-tree", RUNTIME, path)
        assert gitlink == git(tilexr, "ls-tree", CLOCK, path)
        metadata, recorded_path = gitlink.split("\t")
        mode, kind, revision = metadata.split()
        assert (mode, kind, recorded_path) == ("160000", "commit", path)
        dependency = work / f"{name}-git"
        pin(dependency, origin, (revision,))
        archives.append(archive(work, dependency, name, revision))
    bundle = work / "sources.tar"
    with tarfile.open(bundle, "w") as output:
        for path in archives:
            output.add(path, arcname=path.name, recursive=False)
    return bundle


def remote(host, executable, *args, timeout=90, payload=None):
    result = subprocess.run(
        [str(executable), *SSH_OPTIONS, *args], input=payload, capture_output=True, timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(f"{host}: {executable.stem} exited {result.returncode}")
    return result.stdout


def build(host, script, bundle, digest):
    peer = "root@" + host
    staging = ROOT + "/staged-input"
    remote(host, SSH, peer, f"mkdir {staging}")
    remote(host, SSH.with_name("scp.exe"), str(bundle), f"{peer}:{staging}/sources.tar", timeout=300)
    output = remote(host, SSH, peer, f"bash -s -- {digest}", payload=script, timeout=1000)
    receipt = json.loads(output)
    assert receipt["built"] and receipt["revision"] == CANDIDATE
    return {"host": host, **receipt}


def main():
    script = Path(__file__).with_suffix(".sh").read_bytes().replace(b"\r\n", b"\n")
    with tempfile.TemporaryDirectory(prefix="ubx-replicate4-source-", dir=Path.cwd()) as temporary:
        bundle = prepare(Path(temporary).resolve())
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda host: build(host, script, bundle, digest), HOSTS))
    print(json.dumps({
        "built": True, "builds": results, "source_bundle_sha256": digest,
        "github_download_location": "green_workspace",
        "kernel_hashes_match": (
            results[0]["artifacts"]["ubx_dispatch_ep32.o"] ==
            results[1]["artifacts"]["ubx_dispatch_ep32.o"]
        ),
        "npu_workload_started": False,
    }, separators=(",", ":")))


if __name__ == "__main__":
    main()
