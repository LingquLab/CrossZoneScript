"""Retain prepare counts and generate rootinfo without opening the system rootinfo."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def run(args, record):
    require(args["host"] == "141.61.73.107", "unexpected_host")
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args["run_id"]), "invalid_run_id")
    release = Path("/home/c00996840/ai/TileXR-deploy/current").resolve()
    manifest = json.loads((release / "deployment.json").read_text())
    require(manifest["revision"] == args["revision"], "unexpected_revision")
    library = release / "lib/libtile-comm.so"
    require(hashlib.sha256(library.read_bytes()).hexdigest() ==
            args["library_sha256"] == manifest["files"]["lib/libtile-comm.so"], "library_hash_mismatch")
    old = Path("/home/c00996840/ai/TileXR-test/pr229-aiv-p2p-107-67-20261117-02")
    diagnostics = []
    for rank in [0, 1]:
        text = (old / ("ccu_rank%d.log" % rank)).read_text(errors="replace")
        shapes = re.findall(r'invalid direct CCU peer XN/CKE exchange shape[^"\n]*', text)
        traces = [line for line in text.splitlines()
                  if "TileXRDirectCcuTrace" in line and
                  any(key in line for key in ["allocation", "resourceRequest", "resourceSpec", "remoteXn="])]
        diagnostics.append({"rank": rank, "shape_messages": list(dict.fromkeys(shapes))[:2],
                            "resource_trace": [line[:600] for line in traces[:6]]})
    record["previous_failure"] = diagnostics
    config = Path("/home/c00996840/ai/TileXR-deploy/config") / args["run_id"]
    config.parent.mkdir(exist_ok=True)
    config.mkdir(exist_ok=False)
    record["config_directory"] = str(config)
    tool = shutil.which("mindcluster-tools")
    if tool is None:
        scan = subprocess.run(
            ["find", "/usr/local", "/opt", "/home/c00996840/ai", "-maxdepth", "5",
             "-type", "f", "-name", "mindcluster-tools"],
            capture_output=True, text=True, timeout=25)
        (config / "tool-discovery.log").write_text(scan.stdout + scan.stderr)
        candidates = [Path(line) for line in scan.stdout.splitlines() if line.startswith("/")]
        tool = next((str(path) for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)
    record["generator"] = tool
    if tool is None:
        record.update(status="PASS", rootinfo_prepared=False,
                      diagnostic_result="generator_not_found", npu_workload_run=False)
        return
    help_result = subprocess.run([tool, "rootinfo", "--help"], capture_output=True, text=True, timeout=20)
    (config / "generator-help.log").write_text(help_result.stdout + help_result.stderr)
    record["generator_help"] = [line[:180] for line in (help_result.stdout + help_result.stderr).splitlines()
                                 if any(key in line.lower() for key in ["output", "topo", "rootinfo"])][:12]
    require(help_result.returncode == 0, "generator_help_failed")
    generated = config / "rootinfo.generated.json"
    command = [tool, "rootinfo", "--output", str(generated)]
    generated_result = subprocess.run(command, cwd=str(config), capture_output=True, text=True, timeout=120)
    (config / "generator.log").write_text(generated_result.stdout + generated_result.stderr)
    record["generator_exit_code"] = generated_result.returncode
    require(generated_result.returncode == 0 and generated.is_file(), "rootinfo_generation_failed")
    require(0 < generated.stat().st_size < 8 * 1024 * 1024, "invalid_rootinfo_size")
    content = json.loads(generated.read_text())
    require(isinstance(content, dict), "unexpected_rootinfo_shape")
    topo_name = content.get("topo_file_path")
    require(isinstance(topo_name, str) and topo_name, "missing_topology_reference")
    original_topo = Path(topo_name)
    if not original_topo.is_absolute():
        original_topo = config / original_topo
    original_topo = original_topo.resolve(strict=True)
    require(original_topo != Path("/etc/hccl_rootinfo.json"), "generator_topology_points_to_rootinfo")
    require(original_topo.is_file() and 0 < original_topo.stat().st_size < 8 * 1024 * 1024,
            "invalid_topology_size")
    topology = config / "topology.json"
    if original_topo != topology:
        shutil.copy2(str(original_topo), str(topology))
    topo = json.loads(topology.read_text())
    content["topo_file_path"] = str(topology)
    rootinfo = config / "rootinfo.json"
    rootinfo.write_text(json.dumps(content, indent=2) + "\n")
    selected = {}
    for item in objects(content):
        if str(item.get("device_id")) in ["6", "7"] and "local_id" in item:
            device = int(item["device_id"])
            endpoints = [address for address in objects(item)
                         if re.fullmatch(r"[0-9a-fA-F]{32}", str(address.get("addr", "")))
                         and isinstance(address.get("ports"), list) and address["ports"]]
            require(endpoints, "selected_device_missing_eid")
            selected[device] = {"device": device, "local_id": int(item["local_id"]),
                                "endpoint_count": len(endpoints)}
    require(set(selected) == {6, 7}, "selected_devices_missing_from_rootinfo")
    local_ids = {item["local_id"] for item in selected.values()}
    routes = [item for item in objects(topo)
              if "local_a" in item and "local_b" in item
              and {int(item["local_a"]), int(item["local_b"])} == local_ids
              and item.get("local_a_ports") and item.get("local_b_ports")]
    require(routes, "selected_devices_have_no_topology_route")
    env_script = config / "set_rootinfo.sh"
    env_script.write_text(
        "# Task-local TileXR topology; source after the release environment.\n"
        "export TILEXR_CCU_HCCL_ROOT_INFO_PATH='" + str(rootinfo) + "'\n"
        "export TILEXR_UDMA_ROOTINFO_PATH='" + str(rootinfo) + "'\n")
    record.update(status="PASS", rootinfo_prepared=True, rootinfo=str(rootinfo), topology=str(topology),
                  rootinfo_sha256=hashlib.sha256(rootinfo.read_bytes()).hexdigest(),
                  topology_sha256=hashlib.sha256(topology.read_bytes()).hexdigest(),
                  selected_devices=[selected[key] for key in [6, 7]], route_count=len(routes),
                  ctp_route_count=sum("UB_CTP" in item.get("protocols", []) for item in routes),
                  environment_script=str(env_script), system_rootinfo_seed_used=False, npu_workload_run=False)


if __name__ == "__main__":
    settings = json.loads(base64.urlsafe_b64decode(sys.argv[1]).decode())
    summary = {"status": "FAIL", "rootinfo_prepared": False, "npu_workload_run": False}
    try:
        run(settings, summary)
    except Exception as error:
        summary.update(error_type=type(error).__name__, reason=str(error)[:160])
    if "config_directory" in summary:
        (Path(summary["config_directory"]) / "prepare-result.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("CZ_DEBUG_PREPARE " + json.dumps(summary))
    sys.exit(0 if summary["status"] == "PASS" else 1)
