"""Run portable Combine HT benchmarks on an idle server and download profiles to green."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

from run import SSH

ROOT = '"$HOME/.cache/tilexr-ht-be110cc8"'
MODES = {"reduce": "bench_gateway_reduce.py", "send": "bench_send.py"}


def remote_benchmark(ssh, config, campaign):
    revision = config["revision"]
    mode = config["mode"]
    script_name = MODES[mode]
    flags = ["--idle-timeout", str(config.get("idle_timeout", 2700))]
    if config["phase"] == "scale":
        flags += ["--server-count", "1", "--chunks", "256", "--cores", "1" if mode == "send" else "8"]
    command = "bash -s -- " + shlex.join([revision, campaign, script_name, mode, *flags])
    code = Path(__file__).with_name("matrix.sh").read_bytes().replace(b"\r\n", b"\n")
    proc = subprocess.run(ssh + [command], input=code, capture_output=True, timeout=config["timeout"])
    rows = proc.stdout.decode("utf-8", errors="replace").splitlines()
    for line in rows:
        if line.startswith(("completed mode=", "diagnostic=", "stage send,", "stage reduce,", '{"stage": "trace_selection"')):
            print(line[:200])
    print(json.dumps({"stage": "remote_benchmark", "phase": config["phase"], "mode": mode,
                      "exit": proc.returncode, "stderr_bytes": len(proc.stderr), "campaign": campaign}))
    return proc.returncode == 0


def download(ssh, campaign, config):
    target = (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CrossZone" / "tilexr" /
              "be110cc8-10e5-4019-8733-a69b29821b7f" / campaign)
    target.mkdir(parents=True, exist_ok=True)
    files = ["report.json", "rank-times.csv"]
    if config["phase"] == "matrix":
        files += ["selected-traces"]
    args = shlex.join(files)
    pending = target / "profiles.tar.gz.part"
    with pending.open("wb") as archive:
        proc = subprocess.run(ssh + [f"tar -czf - -C {ROOT}/{campaign} {args}"],
                              stdout=archive, stderr=subprocess.PIPE, timeout=600)
    if proc.returncode:
        print(json.dumps({"stage": "download", "exit": proc.returncode, "stderr_bytes": len(proc.stderr)}))
        return
    finished = pending.with_suffix("")
    pending.replace(finished)
    sha = hashlib.sha256()
    with finished.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    print(json.dumps({"stage": "download", "green_file": str(finished),
                      "bytes": finished.stat().st_size, "sha256": sha.hexdigest()}))
    proc = subprocess.run(ssh + [f"cat {ROOT}/{campaign}/report.json"], capture_output=True, timeout=25)
    if proc.returncode == 0:
        report = json.loads(proc.stdout)
        print(json.dumps({"stage": "report", "data": report}, separators=(",", ":")))


def run(config):
    assert re.fullmatch(r"[0-9a-f]{40}", config["revision"])
    assert config["mode"] in MODES and config["phase"] in ("scale", "matrix")
    campaign = f'{config["phase"]}-{config["mode"]}-{config["revision"][:12]}'
    ssh = SSH + [config.get("host", "public_fp8")]
    script = Path(__file__).with_name("collect.py").read_bytes().replace(b"\r\n", b"\n")
    upload = f"umask 077 && cat > {ROOT}/collect-{campaign}.py"
    transfer = subprocess.run(ssh + [upload], input=script, capture_output=True, timeout=40)
    if transfer.returncode:
        print(json.dumps({"stage": "trace_helper_upload", "exit": transfer.returncode}))
        return False
    if not remote_benchmark(ssh, config, campaign):
        return False
    download(ssh, campaign, config)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    run(json.loads(parser.parse_args().args_file.read_text(encoding="utf-8")))


if __name__ == "__main__":
    try:
        main()
    except subprocess.TimeoutExpired:
        print(json.dumps({"stage": "ssh", "error": "deadline_expired"}))
