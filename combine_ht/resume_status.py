"""Read saved evidence and process state for the explicitly continued HT task."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

CONTAINER = "tilexr-combine-ht-f71d0252-v11"
REMOTE = r'''
import json, pathlib, subprocess
c = "tilexr-combine-ht-f71d0252-v11"
def run(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    assert p.returncode == 0, "status_command_failed"
    return p.stdout
identity = json.loads(run(["docker", "inspect", c]))[0]
mounts = identity["Mounts"]
work = next(pathlib.Path(m["Source"]) for m in mounts if m["Destination"] == "/work")
expected = pathlib.Path("/var/tmp/tilexr-combine-ht-f71d0252-ef1e-4b5c-b299-c2429ebcfbdf-v11")
assert work == expected, "unexpected_container_mount"
out = {"ok": True, "read_only": True,
       "container": {"id": identity["Id"], "image": identity["Image"],
                     "running": identity["State"]["Running"],
                     "network_mode": identity["HostConfig"]["NetworkMode"],
                     "session_label": identity["Config"].get("Labels", {}).get("crosszone.session"),
                     "work_mount": str(work)}}
top = run(["docker", "top", c, "-eo", "pid,comm"])
out["sst_pids"] = [line.split()[0] for line in top.splitlines()[1:]
                   if line.split()[-1] == "sstsim.x"]
out["saved"] = {}
for name in ("diagnostic-stdev-trace.json", "diagnostic-stdev-ir.json", "run-summary.json"):
    p = work / name
    if not p.exists():
        out["saved"][name] = {"found": False}
        continue
    assert p.stat().st_size < 131072, "saved_summary_too_large"
    value = json.loads(p.read_text())
    if name == "diagnostic-stdev-trace.json":
        keep = ("ok", "simulator_exit", "wall_s", "report", "probe_sha256", "fatals", "completions", "regions", "instruction_records", "model_shift_context")
        value = {k: value[k] for k in keep if k in value}
    elif name == "diagnostic-stdev-ir.json":
        keep = ("ok", "compile_exit", "report", "bitcode_sha256", "call_count", "calls", "nearby_address_operations")
        value = {k: value[k] for k in keep if k in value}
    else:
        keep = ("ok", "image_id", "revisions", "kernel_sha256", "result", "stage")
        value = {k: value[k] for k in keep if k in value}
    out["saved"][name] = value
probe = work / "diagnostics/stdev-address-a1"
out["address_probe"] = {"directory_exists": probe.is_dir()}
if probe.is_dir():
    receipt = probe / "run.json"
    out["address_probe"]["run"] = json.loads(receipt.read_text()) if receipt.exists() else None
    out["address_probe"]["files"] = sorted(x.name for x in probe.iterdir())[:24]
    out["address_probe"]["logs"] = {}
    for name in ("build.log", "model.log", "sim.log"):
        path = probe / name
        if path.is_file():
            data = path.read_bytes()
            lines = data.decode(errors="replace").splitlines()
            selected = [x[:400] for x in lines if any(word in x.lower() for word in
                ("error:", "fatal:", "assertion", "traceback", "simulation is complete", "unknown low level"))]
            out["address_probe"]["logs"][name] = {"bytes": len(data), "selected": selected[:6]}
out.pop("saved")
print(json.dumps(out))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    assert args == {}, "unexpected_arguments"
    key = Path(os.environ["USERPROFILE"]) / ".ssh/id_ed25519"
    ssh = ["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes", "-o",
           "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes", "-o",
           "ConnectTimeout=12", "root@141.61.94.26", shlex.join(["python3", "-"])]
    p = subprocess.run(ssh, input=REMOTE.encode(), capture_output=True, timeout=90)
    if p.returncode:
        print(json.dumps({"ok": False, "error": "status_transport_failed", "exit_code": p.returncode}))
        return 1
    result = json.loads(p.stdout)
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
