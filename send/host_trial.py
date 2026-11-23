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
from store_controls import BASE_KINDS, POINTER_KINDS, COMPENSATION_KINDS, RUNTIME_KINDS, VECTOR_KINDS, GATHER_KINDS, KINDS, summarize_batch
from interleave_contract_host import PRIOR as CONTRACT_PRIOR, extract as extract_contract
from probe_artifacts import inspect as inspect_probe_artifacts
from builder_controls import KINDS as MASK_BUILDERS
from vf_stack_control import BUILDERS as STACK_BUILDERS

SESSION = "7626e689-38e9-468b-9a82-63673422e9d3"
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
    assert type(args.get("reference_compat", False)) is bool
    assert type(args.get("vf_stack_off", False)) is bool
    assert not args.get("vf_stack_off") or args.get("controls") == "vf-stack"
    assert not args.get("reference_compat") or args.get("mte_status") is True
    assert type(args.get("backward_record_bytes", 24)) is int and args.get("backward_record_bytes", 24) in (24, 32)
    assert not args.get("mte_status") or args.get("capture") is True
    boundary = args.get("stop_boundary", "none")
    assert boundary in ("none", "before_builder", "before_sq_publish")
    assert boundary == "none" or args.get("capture") is True
    assert type(args["attempt"]) is int and 1 <= args["attempt"] <= 31
    builder = args.get("builder", "original")
    assert builder in ("original", "interleave", "interleave_packed", "scalar_control", "scalar_simd_probe", "scalar_simd_gather", "scalar_simd_interleave") + tuple("scalar_" + kind for kind in KINDS) + MASK_BUILDERS
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
    call(["docker", "exec", NAME, "python3", "/work/trial.py", json.dumps(args)],
         timeout=510 if builder == "interleave_predicate_runtime" else 400)
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


def run_store_controls(args):
    assert args["backward_record_bytes"] == 32 and args["mte_status"] is True
    assert socket.gethostname().split(".")[0] == args["hostname"] == "host-94-26"
    assert args.get("controls", "full-tile") in ("full-tile", "pointer", "compensation", "runtime", "vector", "sst-compat", "ub-gather", "mask-local", "mask-predicate", "vf-stack")
    kinds = {"full-tile": BASE_KINDS, "pointer": POINTER_KINDS,
             "compensation": COMPENSATION_KINDS, "runtime": RUNTIME_KINDS,
             "vector": VECTOR_KINDS,
             "ub-gather": GATHER_KINDS,
             "mask-local": MASK_BUILDERS[:2],
             "mask-predicate": MASK_BUILDERS[2:],
             "vf-stack": STACK_BUILDERS,
             "sst-compat": ("control", "interleave", "store_post_runtime_vector")}[args.get("controls", "full-tile")]
    def builder_for(kind):
        if args.get("controls") in ("mask-local", "mask-predicate", "vf-stack"):
            return kind
        return kind if args.get("controls") == "sst-compat" and kind == "interleave" else "scalar_" + kind
    path = WORK / ("store-result-" + str(args["attempt"]) + ".json")
    assert not path.exists(), "store_controls_already_attempted"
    for offset, kind in enumerate(kinds):
        assert not (WORK / f"mte-{builder_for(kind)}-{args['attempt'] + offset}").exists()
    out = {"ok": False, "diagnostic_only": True, "results": [],
           "controls": args.get("controls", "full-tile")}
    if args.get("controls") in ("vector", "sst-compat"):
        previous = "mte-scalar_store_post_runtime-18" if args["controls"] == "vector" else "mte-scalar_store_post_runtime_vector-20"
        out["artifacts"] = [inspect_probe_artifacts(WORK / previous)]
    if args.get("controls") == "ub-gather":
        out["artifacts"] = [inspect_probe_artifacts(WORK / "mte-scalar_store_post_runtime_vector-23")]
    if args.get("controls") == "mask-predicate":
        out["artifacts"] = [inspect_probe_artifacts(WORK / case, "BuildPutWqes") for case in
                            ("mte-interleave-22", "mte-interleave_local_mask-26")]
        pairs = [{row["name"]: row.get("code_sha256") for row in artifact.get("functions", [])}
                 for artifact in out["artifacts"]]
        if pairs[0] and all(pairs[0].values()) and all(pairs[1].values()):
            out["local_mask_vf_codes_identical"] = pairs[0] == pairs[1]
    source = CONTRACT_PRIOR / "mte-interleave-1/compiler-evidence-1/input.ii"
    assert 0 < source.stat().st_size < 16000000
    functions, snippets = extract_contract(source.read_text())
    assert (functions and functions[0]["destination_by_reference"]
            and functions[0]["source_register_count"] == 1), "post_update_contract_missing"
    out["contract"] = {"functions": functions[:2],
                       "source_snippets": [] if args.get("controls") in ("mask-local", "mask-predicate", "vf-stack") else snippets}
    for offset, kind in enumerate(kinds):
        child = {**args, "action": "mte-trial", "builder": builder_for(kind),
                 "attempt": args["attempt"] + offset, "capture": True,
                 "force_inline": False, "stop_boundary": "before_sq_publish"}
        if args.get("controls") in ("sst-compat", "ub-gather", "mask-local", "mask-predicate"):
            child["reference_compat"] = True
        if args.get("controls") == "vf-stack":
            child.update(vf_stack_off=True, reference_compat=offset != 0)
        expected = f"/work/mte-{child['builder']}-{child['attempt']}"
        try:
            with (WORK / "store-host.log").open("a") as log, redirect_stdout(log):
                main(child)
        except Exception as error:
            out.setdefault("host_errors", []).append({"builder": child["builder"], "error": type(error).__name__})
        report = WORK / Path(expected).relative_to("/work") / "trial-result.json"
        if not report.is_file():
            break
        result = json.loads(report.read_text())
        assert result["report"] == expected
        if not result.get("build_completed"):
            build_log = report.parent / "build.log"
            if build_log.is_file():
                result["compile_errors"] = [line[:220] for line in build_log.read_text(errors="replace").splitlines()
                                            if "error:" in line][:2]
        out["results"].append(result)
        if args.get("controls") == "vf-stack" and result.get("build_completed"):
            fragment = "ProbeOperation" if child["builder"].startswith("scalar_") else "BuildPutWqes"
            baseline_case = result["vf_stack_control"]["baseline_case"]
            prior_artifact = inspect_probe_artifacts(WORK / baseline_case, fragment)
            new_artifact = inspect_probe_artifacts(report.parent, fragment)
            old = {row["name"]: row.get("code_sha256") for row in prior_artifact.get("functions", [])}
            new = {row["name"]: row.get("code_sha256") for row in new_artifact.get("functions", [])}
            complete = bool(old and old.keys() == new.keys() and all(old.values()) and all(new.values()))
            result["vf_stack_control"]["vf_code_comparison_complete"] = complete
            if complete:
                result["vf_stack_control"].update(vf_function_count=len(old),
                    changed_vf_function_count=sum(old[name] != new[name] for name in old))
        if args.get("controls") == "mask-local" and offset == 0:
            try:
                out["ir_focus"] = json.loads(call([
                    "docker", "exec", NAME, "python3", "/work/vf_focus.py"], timeout=30))
            except Exception as error:
                out["ir_focus"] = {"available": False, "error": type(error).__name__}
        if args.get("controls") == "ub-gather" and offset == 0:
            try:
                out["compiler_artifacts"] = json.loads(call([
                    "docker", "exec", NAME, "python3", "/work/vf_ir.py", "--inventory"], timeout=30))
            except Exception as error:
                out["compiler_artifacts"] = {"available": False, "error": type(error).__name__}
        if args.get("controls") in ("vector", "sst-compat", "ub-gather") and result.get("build_completed"):
            out["artifacts"].append(inspect_probe_artifacts(report.parent))
        if args.get("controls") == "mask-predicate" and result.get("build_completed"):
            out["artifacts"].append(inspect_probe_artifacts(report.parent, "BuildPutWqes"))
        path.write_text(json.dumps(out, indent=2))
        (WORK / "store-result.json").write_text(json.dumps(out, indent=2))
        if args.get("controls") in ("compensation", "runtime", "ub-gather") and not result["ok"]:
            break
        if args.get("controls") == "sst-compat" and offset == 0 and not result["ok"]:
            break
        if args.get("controls") == "vf-stack" and offset == 0 and not result["ok"]:
            break
        if args.get("controls") == "mask-local" and result.get("returncode") != 0:
            break
        # Continue only after an observed terminal simulator exit, never a timeout.
        if not result["ok"] and result.get("returncode") not in (0, 255):
            break
    out["ok"] = (len(out["results"]) == len(kinds) and all(row["ok"] for row in out["results"])
                 and not out.get("host_errors"))
    path.write_text(json.dumps(out, indent=2))
    (WORK / "store-result.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(summarize_batch(out), separators=(",", ":")))
    return out["ok"]


if __name__ == "__main__":
    try:
        args = json.loads(sys.argv[1])
        if args["action"] == "store-controls":
            sys.exit(0 if run_store_controls(args) else 1)
        if args["action"] in ("boundary-probe", "simd-probes"):
            sys.exit(0 if probe_boundaries(args) else 1)
        main(args)
    except Exception as e:
        print(json.dumps({"ok": False, "error": type(e).__name__, "detail": str(e)[:150]}))
        sys.exit(1)
