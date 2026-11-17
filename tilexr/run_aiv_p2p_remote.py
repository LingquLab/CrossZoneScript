"""Verify a deployed PR, run one two-rank AIV fanout, and retain bounded evidence."""

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def occupancy(device):
    record = {"device": device, "state": "indeterminate"}
    try:
        result = subprocess.run(["npu-smi", "info", "-t", "proc-mem", "-i", str(device)],
                                capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            if re.search(r"Process id\s*:", result.stdout):
                record["state"] = "busy"
            elif re.search(r"NPU ID\s*:\s*" + str(device) + r"(?:\s|$)", result.stdout):
                record["state"] = "idle"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return record


def devices_state():
    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(occupancy, [2, 3]))


def run(args, record):
    require(args["devices"] == [2, 3], "unexpected_device_scope")
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args["run_id"]), "invalid_run_id")
    release = Path(args.get("release", "/home/c00996840/ai/TileXR-deploy/current")).resolve()
    require(release.is_relative_to(Path("/home/c00996840/ai").resolve()), "release_outside_project_parent")
    manifest = json.loads((release / "deployment.json").read_text())
    require(manifest["revision"] == args["revision"], "deployed_revision_mismatch")
    for relative, expected in manifest["files"].items():
        path = release / relative
        require(path.resolve().is_relative_to(release), "manifest_path_outside_release")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == expected, "deployment_hash_mismatch")
    for relative, expected in args["artifact_hashes"].items():
        require(manifest["files"][relative] == expected, "pinned_artifact_hash_mismatch")
    source = Path(manifest["source_directory"])
    runner = source / "tests/ccu/run_tilexr_ccu_direct_smoke.sh"
    require(hashlib.sha256(runner.read_bytes()).hexdigest() == args["runner_sha256"], "runner_hash_mismatch")
    rootinfo = Path(args["rootinfo"]).resolve(strict=True)
    config_parent = Path("/home/c00996840/ai/TileXR-deploy/config").resolve(strict=True)
    require(rootinfo.is_relative_to(config_parent), "rootinfo_outside_project_config")
    require(hashlib.sha256(rootinfo.read_bytes()).hexdigest() == args["rootinfo_sha256"], "rootinfo_hash_mismatch")
    root_content = json.loads(rootinfo.read_text())
    topology = Path(root_content["topo_file_path"]).resolve(strict=True)
    require(topology.parent == rootinfo.parent, "topology_outside_task_config")
    require(hashlib.sha256(topology.read_bytes()).hexdigest() == args["topology_sha256"], "topology_hash_mismatch")
    record.update(rootinfo=str(rootinfo), rootinfo_sha256=args["rootinfo_sha256"],
                  topology=str(topology), topology_sha256=args["topology_sha256"])
    work = Path("/home/c00996840/ai/TileXR-test") / args["run_id"]
    work.parent.mkdir(exist_ok=True)
    work.mkdir(exist_ok=False)
    record.update(work_directory=str(work), release=str(release), direction="2->3",
                  payload_bytes=[128, 256, 384, 512], expected_rounds=4,
                  artifact_hashes=args["artifact_hashes"], runner_sha256=args["runner_sha256"])
    record["pre_test_devices"] = devices_state()
    require(all(item["state"] == "idle" for item in record["pre_test_devices"]), "selected_device_busy_or_indeterminate")
    for original, destination in {
        "bin/ccu_tilexr_direct_smoke_probe": "ccu_tilexr_direct_smoke_probe",
        "lib/libtilexr_ccu_aiv_fanout_kernel.so": "libtilexr_ccu_aiv_fanout_kernel.so",
        "lib/tilexr/kernels/ccu_tilexr_aiv_fanout_kernel.o": "tilexr/kernels/ccu_tilexr_aiv_fanout_kernel.o",
    }.items():
        target = work / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(release / original), str(target))

    # Keep a task-local health allowance for this explicitly requested bounded smoke.
    guard_path = source / "tests/ccu/ccu_npu_smi_busy_guard.py"
    require(hashlib.sha256(guard_path.read_bytes()).hexdigest() == args["guard_sha256"], "health_guard_hash_mismatch")
    spec = importlib.util.spec_from_file_location("ccu_guard", str(guard_path))
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    smi = subprocess.run(["npu-smi", "info"], capture_output=True, text=True, timeout=20)
    (work / "pre-test-npu-smi.log").write_text(smi.stdout + smi.stderr)
    require(smi.returncode == 0, "npu_health_query_failed")
    unhealthy, observed = guard.parse_unhealthy_devices(smi.stdout, {2, 3})
    require(observed == {2, 3}, "selected_device_health_indeterminate")
    allowed_health = set(args.get("allowed_health", ["OK", "ALARM"]))
    require(allowed_health <= {"OK", "ALARM", "WARNING"} and "OK" in allowed_health,
            "invalid_health_allowance")
    record["selected_health"] = [{"device": device, "health": dict(unhealthy).get(device, "OK")}
                                 for device in [2, 3]]
    record["allowed_health"] = sorted(allowed_health)
    require(all(health.upper() in allowed_health for _, health in unhealthy), "selected_device_health_unusable")
    require(not guard.parse_busy_processes(smi.stdout, {2, 3}), "selected_device_became_busy")
    record["alarm_devices"] = [device for device, health in unhealthy if health.upper() == "ALARM"]
    record["warning_devices"] = [device for device, health in unhealthy if health.upper() == "WARNING"]

    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("TILEXR_", "HCCL_", "MOONEP_"))
                   and key not in ("ASCEND_RT_VISIBLE_DEVICES", "ASCEND_VISIBLE_DEVICES", "DEVICE_ID", "RANK_ID", "RANK_SIZE")}
    environment.update({
        "ASCEND_HOME_PATH": "/usr/local/Ascend/cann-9.1.0", "ASCEND_DRIVER_PATH": "/usr/local/Ascend/driver",
        "TILEXR_RUN_CCU_DIRECT_SMOKE_PROBE": "1", "TILEXR_CCU_RANK_SIZE": "2",
        "TILEXR_CCU_SMOKE_DEVICES": "2,3", "TILEXR_TEST_DEVICES": "2,3",
        "TILEXR_TILE_COMM_LIB": str(release / "lib/libtile-comm.so"),
        "TILEXR_CCU_HCCL_ROOT_INFO_PATH": str(rootinfo),
        "TILEXR_UDMA_ROOTINFO_PATH": str(rootinfo),
        "TILEXR_CCU_SMOKE_WORK_DIR": str(work), "TILEXR_CCU_SMOKE_TIMEOUT": "90",
        "TILEXR_CCU_SMOKE_REUSE_PROBE": "1", "TILEXR_CCU_SMOKE_REUSE_FANOUT_KERNEL": "1",
        "TILEXR_CCU_SMOKE_REQUIRE_NPU_SMI": "1", "TILEXR_CCU_SMOKE_ALLOW_BUSY_NPU": "0",
        "TILEXR_CCU_SMOKE_ALLOW_UNHEALTHY_NPU": "1" if unhealthy else "0",
        "TILEXR_CCU_DIRECT_SMOKE_AIV_FANOUT": "1", "TILEXR_CCU_AIV_FANOUT_LOOP": "1",
        "TILEXR_CCU_AIV_FANOUT_ALL_RANKS": "0", "TILEXR_CCU_AIV_FANOUT_INCLUDE_SELF": "0",
        "TILEXR_CCU_AIV_FANOUT_MAPPING_ONLY": "0", "TILEXR_CCU_AIV_FANOUT_PERF": "0",
        "TILEXR_CCU_AIV_FANOUT_POOL_BYTES": "4096",
        "TILEXR_CCU_AIV_FANOUT_DESCRIPTORS": "1:0:256:128",
        "TILEXR_CCU_AIV_FANOUT_ROUNDS": "1:0:256:128|1:128:512:256|1:512:0:384|1:1024:1024:512",
    })
    if args.get("trace", False):
        environment.update(TILEXR_CCU_DIRECT_TRACE="1", TILEXR_CCU_DIRECT_TRACE_ENDPOINT_ROUTE="1",
                           ASCEND_PROCESS_LOG_PATH=str(work / "plog"))
        record["trace_enabled"] = True
    require(isinstance(args.get("force_peer_endpoints", False), bool), "invalid_peer_endpoint_switch")
    if args.get("force_peer_endpoints", False):
        environment["TILEXR_CCU_DIRECT_FORCE_PEER_ENDPOINTS"] = "1"
        record["force_peer_endpoints"] = True
    if "install_die" in args:
        require(type(args["install_die"]) is int and args["install_die"] in [0, 1], "invalid_install_die")
        environment["TILEXR_CCU_DIRECT_INSTALL_DIE_ID"] = str(args["install_die"])
        record["install_die"] = args["install_die"]
    record["stage"] = "hardware-run"
    started = time.monotonic()
    with (work / "runner.log").open("wb") as output:
        process = subprocess.Popen(["bash", str(runner)], stdout=output, stderr=subprocess.STDOUT,
                                   cwd=str(source), env=environment, start_new_session=True)
        try:
            status = process.wait(timeout=135)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            status = 124
    record.update(runner_exit_code=status, elapsed_seconds=round(time.monotonic() - started, 3))
    text = (work / "runner.log").read_text(errors="replace")
    record["npu_tests_run"] = " launch rank=" in text
    results = []
    for rank, device in enumerate([2, 3]):
        rank_log = work / ("ccu_rank%d.log" % rank)
        lines = rank_log.read_text(errors="replace").splitlines() if rank_log.is_file() else []
        summaries = [line for line in lines if line.startswith("tilexr_ccu_aiv_fanout result ")]
        tokens = dict(re.findall(r"(\w+)=([^\s]+)", summaries[-1])) if summaries else {}
        fields = ["rank", "rankSize", "device", "active", "roundCount", "roundPassedCount",
                  "hostSubmitCallCount", "hostCcuLaunchCount", "aivLaunchCount", "aivSyncRet", "ccuSyncRet",
                  "destinationReadRet", "debugReadRet", "debugStage", "debugProgress", "mappingOnly", "mappingReady", "mappingReleaseRet", "mismatches", "debugMismatches", "passed"]
        values = {key: int(tokens[key]) for key in fields if key in tokens and re.fullmatch(r"-?\d+", tokens[key])}
        values["expected_device"] = device
        values["expected_rank"] = rank
        values["passed_round_lines"] = sum(line.startswith("tilexr_ccu_aiv_fanout round ") and "passed=1" in line for line in lines)
        values["prepare_diagnostics"] = [line[:950] for line in lines
                                         if "prepare ret=" in line][:2]
        if args.get("trace", False):
            traces = [line for line in lines if any(key in line for key in [
                "peerEndpoint created", "peerEndpoint imported", "endpointRoute selectedTp",
                "decoded=PfeCtx", "decoded=ChannelCtx", "decoded=LocalJettyCtx", "afterAiv", "failureDebug",
                "syncResource[", "remoteXnBinding[", "decoded=TransLocMemToRmtMem"])]
            values["trace_summary"] = [re.sub(r'\b\w*(?:[Tt]oken|[Ee]id|Handle|Addr|Va)\w*=\S+',
                                              '<omitted>', re.sub(r'\brawWords=\S+', '<omitted>', line))[:800]
                                       for line in traces[:26]]
        results.append(values)
    record["rank_results"] = results
    record["post_test_devices"] = devices_state()
    for _ in range(4):
        if all(item["state"] == "idle" for item in record["post_test_devices"]):
            break
        time.sleep(2)
        record["post_test_devices"] = devices_state()
    require(status == 0, "hardware_runner_failed")
    require("tilexr_ccu_direct_smoke_runner success " in text, "runner_success_marker_missing")
    for rank, device in enumerate([2, 3]):
        item = results[rank]
        require(item.get("rank") == rank and item.get("device") == device and item.get("rankSize") == 2,
                "rank_device_mapping_mismatch")
        require(item.get("passed") == 1 and item.get("mismatches") == 0 and item.get("mappingOnly") == 0,
                "payload_validation_failed")
        require(item.get("hostSubmitCallCount") == item.get("hostCcuLaunchCount") == item.get("aivLaunchCount") == (1 if rank == 0 else 0),
                "launch_count_mismatch")
    require(results[0].get("roundPassedCount") == results[0].get("roundCount") == results[0]["passed_round_lines"] == 4,
            "round_validation_failed")
    require(results[0].get("debugMismatches") == 0 and results[0].get("aivSyncRet") == results[0].get("ccuSyncRet") == 0,
            "kernel_or_ccu_completion_failed")
    require(all(item["state"] == "idle" for item in record["post_test_devices"]), "post_test_occupancy_not_idle")
    record.update(status="PASS", stage="complete")


if __name__ == "__main__":
    settings = json.loads(base64.urlsafe_b64decode(sys.argv[1]).decode())
    summary = {"status": "FAIL", "stage": "preflight", "revision": settings["revision"],
               "devices": [2, 3], "npu_tests_run": False}
    try:
        run(settings, summary)
    except Exception as error:
        summary.update(error_type=type(error).__name__, reason=str(error)[:160])
    if "work_directory" in summary:
        (Path(summary["work_directory"]) / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("CZ_AIV_P2P " + json.dumps(summary))
    sys.exit(0 if summary["status"] == "PASS" else 1)
