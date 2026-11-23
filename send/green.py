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

SESSION = "7626e689-38e9-468b-9a82-63673422e9d3"
WORK = "/var/tmp/tilexr-combine-ht-" + SESSION + "-v11"


def main(args):
    assert args["ssh_target"] == "root@141.61.94.26"
    assert args["action"] in ("mte-trial", "boundary-probe", "boundary-result", "simd-probes", "simd-result", "store-controls", "store-result", "padding-probes", "padding-result", "vf-inspect", "vf-inspection-result", "vf-ir", "vf-ir-result", "vf-text", "vf-text-result", "vf-decode", "vf-decode-result", "vf-asm", "vf-asm-result", "vf-machine", "vf-machine-result", "trial-result", "outline", "gather-contract", "compiler-inspect", "object-inspect", "call-focus", "compiler-result")
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
    if args["action"] in ("mte-trial", "boundary-probe", "simd-probes", "store-controls"):
        names, entry, budget = ("host_trial.py", "trial.py", "builder_controls.py", "vf_stack_control.py", "packed_builder.py", "backward_alignment.py", "simd_probe_variants.py", "store_controls.py", "probe_artifacts.py", "mte_state.h", "simd_wqe_interleave.h", "scalar_wqe_control.h", "minimal_simd_probe.h", "interleave_contract_host.py"), "host_trial.py", 430
        if args["action"] in ("boundary-probe", "simd-probes"):
            budget = 850
        if args["action"] == "store-controls":
            budget = 1260
            if args.get("controls") == "ub-gather":
                names += ("vf_ir.py", "vf_inspect.py")
                budget = 880
            if args.get("controls") == "mask-local":
                names += ("vf_focus.py",)
                budget = 880
            if args.get("controls") == "mask-predicate":
                names += ("vf_codegen.py", "vf_ir.py", "vf_inspect.py")
                budget = 620
            if args.get("controls") == "vf-packed":
                names += ("vf_codegen.py", "vf_ir.py", "vf_inspect.py")
                budget = 620
    elif args["action"] == "padding-probes":
        names = ("padding_probe.py", "trial.py", "builder_controls.py", "packed_builder.py", "backward_alignment.py", "simd_probe_variants.py", "store_controls.py")
        entry, budget = "padding_probe.py", 640
    elif args["action"] == "vf-inspect":
        names, entry, budget = ("vf_inspect.py",), "vf_inspect.py", 330
    elif args["action"] == "vf-ir":
        names, entry, budget = ("vf_ir.py", "vf_inspect.py"), "vf_ir.py", 510
    elif args["action"] == "vf-text":
        names, entry, budget = ("vf_text.py", "vf_ir.py", "vf_inspect.py"), "vf_text.py", 270
    elif args["action"] == "vf-decode":
        names, entry, budget = ("vf_decode.py", "vf_text.py", "vf_ir.py", "vf_inspect.py"), "vf_decode.py", 330
    elif args["action"] == "vf-asm":
        names, entry, budget = ("vf_asm.py", "vf_inspect.py"), "vf_asm.py", 290
    elif args["action"] == "vf-machine":
        names, entry, budget = ("vf_machine.py", "vf_inspect.py"), "vf_machine.py", 360
    elif args["action"] in ("compiler-inspect", "object-inspect", "call-focus", "compiler-result"):
        names, entry, budget = ("compiler_inspect.py",), "compiler_inspect.py", 210
    else:
        names, entry, budget = ("evidence.py", "store_controls.py"), "evidence.py", 40
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
