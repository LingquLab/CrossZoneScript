# Copyright (c) 2026 TileXR Project.
"""Create this session's CPU SST container and run one bounded Send candidate.

The old task directory is mounted read-only at /baseline. Detailed process
captures and all new artifacts stay in this session's separate /work directory.
"""
import json
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import socket
import subprocess
import sys

SESSION = "05d1d635-d6d7-4dd3-af34-421e32bb209c"
NAME = "tilexr-combine-ht-" + SESSION[:8] + "-v11"
IMAGE = "sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f"
WORK = Path(__file__).resolve().parent


def call(args, timeout=40):
    p = subprocess.run(args, capture_output=True, timeout=timeout)
    with (WORK / "host-trial.log").open("ab") as f:
        f.write(p.stdout + b"\nSTDERR\n" + p.stderr)
    if p.returncode:
        raise RuntimeError("command_exit_" + str(p.returncode))
    return p.stdout


def main(args):
    assert socket.gethostname().split(".")[0] == args["hostname"] == "host-94-26"
    assert WORK.name == "tilexr-combine-ht-" + SESSION + "-v11"
    prior = Path(args["prior"])
    assert prior.parent == Path("/var/tmp") and prior.name.startswith("tilexr-combine-ht-")
    check = subprocess.run(["docker", "inspect", NAME], capture_output=True, timeout=20)
    if check.returncode:
        call(["docker", "run", "-dt", "--name", NAME, "--label", "crosszone.session=" + SESSION,
              "--network=none", "--runtime=runc", "--shm-size=64m", "--ulimit", "nofile=524288:524288",
              "--mount", "type=bind,src=" + str(WORK) + ",dst=/work",
              "--mount", "type=bind,src=" + str(prior) + ",dst=/baseline,readonly",
              IMAGE, "/bin/bash", "-l"])
    info = json.loads(call(["docker", "inspect", "--format", "{{json .}}", NAME]))
    assert info["Image"] == IMAGE and info["Config"]["Labels"].get("crosszone.session") == SESSION
    assert info["State"]["Running"] and info["HostConfig"]["NetworkMode"] == "none"
    assert info["HostConfig"]["Runtime"] == "runc"
    assert any(m["Source"] == str(prior) and m["Destination"] == "/baseline" and not m["RW"] for m in info["Mounts"])
    assert any(m["Source"] == str(WORK) and m["Destination"] == "/work" and m["RW"] for m in info["Mounts"])
    assert type(args.get("force_inline", False)) is bool
    assert type(args.get("capture", False)) is bool
    assert type(args.get("mte_status", False)) is bool
    assert type(args.get("backward_record_bytes", 24)) is int and args.get("backward_record_bytes", 24) in (24, 32)
    assert not args.get("mte_status") or args.get("capture") is True
    boundary = args.get("stop_boundary", "none")
    assert boundary in ("none", "before_builder", "before_sq_publish")
    assert boundary == "none" or args.get("capture") is True
    assert type(args["attempt"]) is int and 1 <= args["attempt"] <= 20
    builder = args.get("builder", "original")
    assert builder in ("original", "interleave", "interleave_packed", "scalar_control", "scalar_simd_probe", "scalar_simd_gather", "scalar_simd_interleave")
    folder = "mte-" + builder + "-" + str(args["attempt"])
    assert not (WORK / folder).exists(), "candidate already attempted"
    old_result = WORK / "trial-result.json"
    if old_result.exists():
        record = json.loads(old_result.read_text())
        retained = WORK / Path(record["report"]).relative_to("/work") / "trial-result.json"
        if not retained.exists():
            shutil.copyfile(old_result, retained)
    config = WORK / "perf-environment.json"
    if config.exists():
        assert config.read_bytes() == (prior / config.name).read_bytes()
    else:
        shutil.copyfile(prior / config.name, config)
    call(["docker", "exec", NAME, "python3", "/work/trial.py", json.dumps(args)], timeout=400)
    result = json.loads((WORK / "trial-result.json").read_text())
    assert result["ok"]
    result["container_id"] = info["Id"]
    print(json.dumps(result, separators=(",", ":")))


def probe_boundaries(args):
    operation_probes = args["action"] == "simd-probes"
    prefix = "simd" if operation_probes else "boundary"
    path = WORK / (prefix + "-result-" + str(args["attempt"]) + ".json")
    assert not path.exists(), "boundary_probe_already_attempted"
    latest = WORK / (prefix + "-result.json")
    if latest.is_file():
        previous = json.loads(latest.read_text())
        if previous.get("results"):
            first = Path(previous["results"][0]["report"]).name.rsplit("-", 1)[1]
            retained = WORK / (prefix + "-result-" + first + ".json")
            if not retained.is_file():
                shutil.copyfile(latest, retained)
    out = {"ok": False, "diagnostic_only": True, "results": []}
    steps = (("scalar_simd_gather", "before_sq_publish"), ("scalar_simd_interleave", "before_sq_publish")) if operation_probes else (
        (args["builder"], "before_builder"), (args["builder"], "before_sq_publish"))
    for offset, (builder, boundary) in enumerate(steps):
        child = {**args, "action": "mte-trial", "builder": builder, "attempt": args["attempt"] + offset,
                 "capture": True, "force_inline": False, "stop_boundary": boundary}
        try:
            with (WORK / "boundary-host.log").open("a") as log, redirect_stdout(log):
                main(child)
        except Exception as error:
            out.setdefault("host_errors", []).append({"builder": builder, "error": type(error).__name__})
        current = WORK / "trial-result.json"
        expected = "/work/mte-" + child["builder"] + "-" + str(child["attempt"])
        if current.is_file() and json.loads(current.read_text()).get("report") == expected:
            result = json.loads(current.read_text())
            out["results"].append(result)
        else:
            out["missing_report"] = boundary
            break
        path.write_text(json.dumps(out, indent=2))
        latest.write_text(json.dumps(out, indent=2))
        if not result["ok"]:
            if not operation_probes or result.get("returncode") not in (0, 255):
                break
    out["ok"] = len(out["results"]) == 2 and all(result["ok"] for result in out["results"]) and not out.get("host_errors")
    path.write_text(json.dumps(out, indent=2))
    latest.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, separators=(",", ":")))
    return out["ok"]


if __name__ == "__main__":
    try:
        args = json.loads(sys.argv[1])
        if args["action"] in ("boundary-probe", "simd-probes"):
            sys.exit(0 if probe_boundaries(args) else 1)
        main(args)
    except Exception as e:
        print(json.dumps({"ok": False, "error": type(e).__name__, "detail": str(e)[:150]}))
        sys.exit(1)
