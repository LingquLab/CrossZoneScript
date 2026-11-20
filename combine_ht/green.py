# Copyright (c) 2026 TileXR Project.
"""Stage pinned inputs from Windows, then run commands on the green SST host."""

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile

SESSION = "c1d52c19-f831-4174-b368-933ccbabec24"
SOURCE = "233c0c307d08e12c5ed95e76aad08910f5aebc6c"
SOURCE_REF = "codex/combine-ht-sim-c1d52c19"
TOOLING = "23a542b2e6bfcc34f85313d02677a4aafdfc3efb"
TOOLING_REF = "codex/unitest-sst-input-abi"
WORK = "/var/tmp/tilexr-combine-ht-c1d52c19"
CONTAINER = "tilexr-combine-ht-c1d52c19"
ORIGIN = "https://github.com/LingquLab/TileXR.git"


def main(args):
    stage = args["stage"]
    root = Path(os.environ["LOCALAPPDATA"]) / "CrossZone" / "tilexr" / SESSION
    root.mkdir(parents=True, exist_ok=True)
    receipt = root / (stage + ".json")
    ssh = ["ssh", "-T", "-i", str(Path(os.environ["USERPROFILE"]) / ".ssh" / "id_ed25519"),
           "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=12", "root@141.61.94.26"]
    bootstrap = ["bash", "-c", 'set -e; test "$(hostname -s)" = host-94-26; umask 077; mkdir -p "$1"; test -O "$1"', "host", WORK]
    try:
        def run(command, timeout, payload=None):
            command = list(map(str, command))
            with (root / (stage + ".log")).open("ab") as log:
                result = subprocess.run(command, input=payload, capture_output=True, timeout=timeout)
                log.write(result.stderr)
                if result.returncode:
                    log.write(result.stdout)
                    raise RuntimeError("command exited " + str(result.returncode))
                return result.stdout

        def remote(argv, timeout, payload=None):
            return run(ssh + [shlex.join(list(map(str, argv)))], timeout, payload)

        def upload(path, filename):
            remote(["timeout", "120", "bash", "-c", 'set -e; cat > "$1.tmp"; mv "$1.tmp" "$1"',
                    "upload", WORK + "/" + filename], 135, path.read_bytes())

        def scripts():
            archive = root / "scripts.tar"
            with tarfile.open(archive, "w") as package:
                for filename in ("host.py", "sst.py", "fixtures.py", "bf16_probe.cpp", "bf16_probe.py"):
                    target = Path(__file__).with_name(filename)
                    package.add(target, arcname=filename)
            upload(archive, "scripts.tar")
            remote(["tar", "-xf", WORK + "/scripts.tar", "-C", WORK], 20)

        if stage == "setup":
            remote(bootstrap, 25)
            repo = root / "TileXR.git"
            if not repo.exists():
                run(["git", "init", "--bare", repo], 20)
            run(["git", "-C", repo, "fetch", "--no-tags", "--depth=1", ORIGIN,
                 "refs/heads/" + SOURCE_REF + ":refs/heads/candidate",
                 "refs/heads/" + TOOLING_REF + ":refs/heads/tooling"], 240)
            for name, revision, paths in (
                ("candidate", SOURCE, ["include", "src/moonep/combine_ht"]),
                ("tooling", TOOLING, ["tools/unitest/simulator"]),
            ):
                if run(["git", "-C", repo, "rev-parse", "refs/heads/" + name], 15).decode().strip() != revision:
                    raise ValueError("unexpected " + name + " revision")
                archive = root / (name + ".tar")
                run(["git", "-C", repo, "archive", "--format=tar", "-o", archive, revision, *paths], 45)
                upload(archive, archive.name)
        if stage in ("setup", "run", "reduce-fixture", "pair", "bf16-probe", "status", "stop"):
            if stage != "status":
                scripts()
            timeout = 240 if stage == "setup" else 120
            if stage in ("run", "reduce-fixture", "pair", "bf16-probe"):
                timeout = 2 * args["stage_timeout"] + 180
            response = remote(["timeout", "--kill-after=25", str(timeout), "python3", WORK + "/host.py",
                               json.dumps(args, separators=(",", ":"))], timeout + 45)
            result = json.loads(response)
            receipt.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=True))
            return 0 if result["ok"] else 1
        raise ValueError("unknown task stage")
    except (OSError, subprocess.TimeoutExpired, ValueError, RuntimeError) as exc:
        result = {"ok": False, "stage": stage, "error": type(exc).__name__,
                  "detail": str(exc) if type(exc) is RuntimeError else "", "log": str(root / (stage + ".log"))}
        receipt.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    sys.exit(main(json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))))
