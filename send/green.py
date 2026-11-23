# Copyright (c) 2026 TileXR Project.
"""Run this session's bounded inspection script over the existing SSH identity.

Detailed captures stay in the Bridge session directory. Only the small JSON
selected by the remote script is returned to the blue session.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

SESSION = "05d1d635-d6d7-4dd3-af34-421e32bb209c"
WORK = "/var/tmp/tilexr-combine-ht-" + SESSION + "-v11"


def main(args):
    assert args["ssh_target"] == "root@141.61.94.26"
    assert args["action"] in ("mte-trial", "boundary-probe", "boundary-result", "simd-probes", "simd-result", "trial-result", "outline", "gather-contract", "compiler-inspect", "object-inspect", "call-focus", "compiler-result")
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
    if args["action"] in ("mte-trial", "boundary-probe", "simd-probes"):
        names, entry, budget = ("host_trial.py", "trial.py", "packed_builder.py", "backward_alignment.py", "simd_probe_variants.py", "mte_state.h", "simd_wqe_interleave.h", "scalar_wqe_control.h", "minimal_simd_probe.h"), "host_trial.py", 430
        if args["action"] in ("boundary-probe", "simd-probes"):
            budget = 850
    elif args["action"] in ("compiler-inspect", "object-inspect", "call-focus", "compiler-result"):
        names, entry, budget = ("compiler_inspect.py",), "compiler_inspect.py", 210
    else:
        names, entry, budget = ("evidence.py",), "evidence.py", 40
    for name in names:
        source = Path(__file__).with_name(name).read_bytes()
        call(["python3", "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())",
              WORK + "/" + name], source)
    output = call(["timeout", "--kill-after=10", str(budget), "python3", WORK + "/" + entry, json.dumps(args)],
                  timeout=budget + 30)
    result = json.loads(output)
    encoded = json.dumps(result, ensure_ascii=True, separators=(",", ":"))
    assert len(encoded.encode()) <= 12000, "small_packet_budget_exceeded"
    print(encoded)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--args-file", type=Path, required=True)
    try:
        main(json.loads(p.parse_args().args_file.read_text(encoding="utf-8")))
    except Exception as e:
        print(json.dumps({"ok": False, "error": type(e).__name__, "detail": str(e)[:120]}))
        sys.exit(1)
