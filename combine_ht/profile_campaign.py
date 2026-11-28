"""Deploy the pinned profile benchmark and copy reports and selected traces to green."""
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

from run import SSH, StageFailure, bundle, checked


def download(ssh, remote, campaign, mode, target):
    name = f"{mode}.tar.gz"
    pending = target / (name + ".part")
    items = ["report.json", "rank-times.csv"]
    if mode != "tokens":
        items += ["selected-traces"]
    with pending.open("wb") as stream:
        proc = subprocess.run(ssh + [f"tar -czf - -C {remote}/{campaign}/{mode} {shlex.join(items)}"],
                              stdout=stream, stderr=subprocess.PIPE, timeout=600)
    if proc.returncode:
        print(json.dumps({"stage": "download", "mode": mode, "exit": proc.returncode}))
        return
    with tarfile.open(pending) as archive:
        report = json.load(archive.extractfile("report.json"))
        samples = archive.extractfile("rank-times.csv")
        assert sum(1 for _ in samples) == (121 if mode == "tokens" else 25601)
        if mode != "tokens":
            names = archive.getnames()
            assert sum(name.endswith(".records") for name in names) == 24
            assert len(report["cases"]) == 20
    final = target / name
    pending.replace(final)
    digest = hashlib.sha256()
    with final.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    print(json.dumps({"stage": "download", "mode": mode, "file": str(final),
                      "bytes": final.stat().st_size, "sha256": digest.hexdigest()}))
    print(json.dumps({"stage": "report", "data": report}, separators=(",", ":")))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    config = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    args = argparse.Namespace(**config)
    assert re.fullmatch("[0-9a-f]{40}", args.revision)
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    ssh = SSH + [args.host]
    remote = '"$HOME/.cache/tilexr-ht-be110cc8"'
    campaign = "profile-" + args.revision[:12]
    checked("prepare_remote_directory", ssh + [f"umask 077 && mkdir {remote}/{campaign}"], timeout=30)
    with tempfile.TemporaryDirectory() as working:
        archive = bundle(args, Path(working))
        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            stream.seek(0)
            upload = f"umask 077 && cat > {remote}/tilexr-{args.revision}.tar.gz"
            checked("upload", ssh + [upload], stdin=stream, timeout=240)
    scripts = Path(__file__).parent
    for filename in ("collect.py", "profile_worker.py"):
        checked("upload_worker", ssh + [f"umask 077 && cat > {remote}/{campaign}/{filename}"],
                input=(scripts / filename).read_bytes().replace(b"\r\n", b"\n"), timeout=60)
    command = "bash -s -- " + shlex.join([args.revision, digest.hexdigest(), str(args.idle_timeout)])
    proc = subprocess.run(ssh + [command], input=(scripts / "profile.sh").read_bytes().replace(b"\r\n", b"\n"),
                          capture_output=True, timeout=args.timeout)
    print(json.dumps({"stage": "remote_finished", "exit": proc.returncode, "stderr_bytes": len(proc.stderr)}))
    output = proc.stdout.decode("utf-8", errors="replace")
    for line in output.splitlines():
        if line.startswith(("mode=", "build_", "waiting ", "completed ")) or "Error:" in line:
            clean = re.sub(r"/[^\s,:]+", "[path]", line)
            print(clean[:220])
    target = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CrossZone" / "tilexr" / campaign
    target.mkdir(parents=True, exist_ok=True)
    for mode in ("tokens", "reduce", "send"):
        if re.search(fr"^mode={mode} rc=0$", output, re.M):
            download(ssh, remote, campaign, mode, target)


if __name__ == "__main__":
    try:
        main()
    except StageFailure:
        pass
    except subprocess.TimeoutExpired:
        print(json.dumps({"stage": "controller_deadline_expired"}))
