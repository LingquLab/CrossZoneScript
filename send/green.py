# Copyright (c) 2026 TileXR Project.
"""Run this session's bounded inspection script over the existing SSH identity.

Detailed captures stay in the Bridge session directory. Only the small JSON
selected by the remote script is returned to the blue session.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

SESSION = "cb41ec7d-5530-4f29-981d-6274e209a40e"
WORK = "/var/tmp/tilexr-combine-ht-" + SESSION + "-v11"


def main(args):
    assert re.fullmatch(r"root@[0-9.]+", args["ssh_target"])
    key = Path(os.environ["USERPROFILE"]) / ".ssh/id_ed25519"
    ssh = ["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
           "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", args["ssh_target"]]
    log = Path.cwd() / ("send-" + args["action"] + ".log")

    def call(argv, payload=None, timeout=50):
        r = subprocess.run(ssh + [shlex.join(map(str, argv))], input=payload,
                           capture_output=True, timeout=timeout)
        with log.open("ab") as f:
            f.write(r.stdout + b"\nSTDERR\n" + r.stderr)
        if r.returncode:
            raise RuntimeError("remote_exit_" + str(r.returncode))
        return r.stdout

    call(["mkdir", "-p", WORK])
    call(["python3", "-c", "import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.unlink() if p.exists() else None",
          WORK + "/inspect.py"])
    names = ("host_trial.py", "trial.py", "mte_state.h") if args["action"] == "mte-trial" else ("evidence.py",)
    for name in names:
        source = Path(__file__).with_name(name).read_bytes()
        call(["python3", "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())",
              WORK + "/" + name], source)
    entry = "host_trial.py" if args["action"] == "mte-trial" else "evidence.py"
    budget = 430 if args["action"] == "mte-trial" else 40
    output = call(["timeout", "--kill-after=10", str(budget), "python3", WORK + "/" + entry, json.dumps(args)],
                  timeout=budget + 30)
    result = json.loads(output)
    encoded = json.dumps(result, ensure_ascii=True, separators=(",", ":"))
    assert len(encoded.encode()) <= 2300, "small_packet_budget_exceeded"
    print(encoded)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--args-file", type=Path, required=True)
    try:
        main(json.loads(p.parse_args().args_file.read_text(encoding="utf-8")))
    except Exception as e:
        print(json.dumps({"ok": False, "error": type(e).__name__, "detail": str(e)[:120]}))
        sys.exit(1)
