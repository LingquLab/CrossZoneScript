"""Build the candidate core and Host probe, verify reused AIV sources, and run CPU checks."""

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def run(args, record):
    require(args["host"] == "141.61.73.107" and args["devices"] == [2, 3], "invalid_scope")
    require(re.fullmatch(r"[0-9a-f]{40}", args["revision"]), "invalid_revision")
    baseline = Path("/home/c00996840/ai/TileXR-deploy/current").resolve()
    previous = json.loads((baseline / "deployment.json").read_text())
    require(previous["revision"] == "ee779869af25ed1cd23db1bbcde0fa2ad2d347af", "baseline_revision_changed")
    for name, expected in previous["files"].items():
        require(digest(baseline / name) == expected, "baseline_artifact_changed")
    root = Path("/home/c00996840/ai") / ("TileXR-pr229-debug-" + args["revision"][:12])
    record["build_directory"] = str(root)
    source = root / "source"
    resume_packaging = args.get("resume_packaging", False)
    resume = args.get("resume_verified_build", False) or resume_packaging
    if resume:
        saved = json.loads((root / "debug-build-result.json").read_text())
        expected_reason = "'ASCEND_DRIVER_PATH'" if resume_packaging else "host_prepare_regression_failed"
        require(saved["status"] == "FAIL" and saved["core_build"] == "PASS" and
                saved["reason"] == expected_reason, "build_not_resumable")
        require((root / "REVISION").read_text().strip() == args["revision"], "retained_revision_mismatch")
        require((root / "SOURCE_ARCHIVE_SHA256").read_text().strip() ==
                args.get("retained_archive_sha256", args["source_archive_sha256"]),
                "retained_archive_mismatch")
        if resume_packaging:
            packed = base64.b64decode("__CZ_SOURCE_ARCHIVE_B64__")
            require(hashlib.sha256(packed).hexdigest() == args["source_archive_sha256"], "archive_hash_mismatch")
            with tarfile.open(fileobj=io.BytesIO(packed), mode="r:gz") as archive:
                for member in archive.getmembers():
                    require((source / member.name).resolve().is_relative_to(source.resolve()), "archive_path_escape")
                    require(member.isfile() or member.isdir(), "archive_nonregular_entry")
                    if member.isfile():
                        expected = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
                        require(digest(source / member.name) == expected, "retained_source_changed")
        else:
            for relative, expected in args["source_hashes"].items():
                require(digest(source / relative) == expected, "retained_source_changed")
        built_digest = digest(root / "build/src/comm/libtile-comm.so")
        if resume_packaging:
            require(all(digest(root / relative) == built_digest for relative in
                        ["install/lib/libtile-comm.so", "package/lib/libtile-comm.so"]),
                    "retained_library_copies_differ")
        else:
            require(built_digest == args["built_library_sha256"], "retained_library_changed")
        for name in ["debug-build-result.json", "host-check.log", "host-check-build.log"]:
            if (root / name).is_file():
                backup = root / (name + ".before-" + args["run_id"])
                require(not backup.exists(), "resume_backup_already_exists")
                shutil.copy2(str(root / name), str(backup))
    else:
        root.mkdir(exist_ok=False)
        source.mkdir()
        packed = base64.b64decode("__CZ_SOURCE_ARCHIVE_B64__")
        require(hashlib.sha256(packed).hexdigest() == args["source_archive_sha256"], "archive_hash_mismatch")
        with tarfile.open(fileobj=io.BytesIO(packed), mode="r:gz") as archive:
            for member in archive.getmembers():
                require((source / member.name).resolve().is_relative_to(source.resolve()), "archive_path_escape")
                require(member.isfile() or member.isdir(), "archive_nonregular_entry")
            archive.extractall(str(source))
        (root / "REVISION").write_text(args["revision"] + "\n")
        (root / "SOURCE_ARCHIVE_SHA256").write_text(args["source_archive_sha256"] + "\n")

    def logged(argv, name, timeout):
        with (root / (name + ".log")).open("wb") as output:
            result = subprocess.run(argv, stdout=output, stderr=subprocess.STDOUT,
                                    timeout=timeout, cwd=str(source))
        require(result.returncode == 0, name + "_failed")

    build = root / "build"
    install = root / "install"
    if not resume:
        logged(["cmake", "-S", str(source), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
                "-DCMAKE_INSTALL_PREFIX=" + str(install), "-DCMAKE_INSTALL_LIBDIR=lib",
                "-DBUILD_TESTING=OFF", "-DTILEXR_BUILD_TESTS=OFF", "-DTILEXR_BUILD_UNITEST=OFF",
                "-DTILEXR_BUILD_COLLECTIVES=OFF", "-DTILEXR_BUILD_EP=OFF"], "configure", 120)
        logged(["cmake", "--build", str(build), "--target", "tile-comm", "-j16"], "build", 480)
        logged(["cmake", "--install", str(build)], "install", 60)
    record["core_build"] = "PASS"
    record["reused_verified_build"] = resume
    record["core_library_sha256"] = digest(build / "src/comm/libtile-comm.so")
    package = root / "package"
    shutil.copytree(str(baseline), str(package), dirs_exist_ok=resume)
    shutil.copytree(str(install), str(package), dirs_exist_ok=True)
    # Pin the core at the package path selected by every probe, regardless of GNUInstallDirs.
    shutil.copy2(str(build / "src/comm/libtile-comm.so"), str(package / "lib/libtile-comm.so"))
    require(digest(package / "lib/libtile-comm.so") == digest(build / "src/comm/libtile-comm.so"),
            "packaged_core_differs_from_build")
    reused_sources = ["tests/ccu/ccu_tilexr_aiv_fanout_kernel.cpp",
                      "tests/ccu/ccu_tilexr_multi_mission_profile.h",
                      "tests/ccu/ccu_tilexr_aiv_fanout_launch.cpp",
                      "src/common/tilexr_kernel_binary_loader.cpp",
                      "src/common/tilexr_kernel_binary_loader.h"]
    baseline_source = Path(previous["source_directory"])
    for relative in reused_sources:
        require(digest(source / relative) == digest(baseline_source / relative),
                "reused_aiv_source_changed")
    record["reused_aiv_source_hashes"] = {name: digest(source / name) for name in reused_sources}
    cann_root = Path(os.environ["ASCEND_HOME_PATH"]) / "aarch64-linux"
    cann_lib = cann_root / "lib64"
    driver_lib = Path(os.environ.get("ASCEND_DRIVER_PATH") or "/usr/local/Ascend/driver") / "lib64/driver"
    require(driver_lib.is_dir(), "driver_library_directory_missing")
    probe_source = source / "tests/ccu/ccu_tilexr_direct_smoke_probe.cpp"
    logged(["c++", "-std=c++14", "-O2", "-DTILEXR_CCU_TESTING=1",
            "-I", str(source / "src/include"), "-I", str(source / "src/comm"),
            "-I", str(cann_root / "pkg_inc"), "-I", str(cann_root / "pkg_inc/runtime"),
            "-I", str(cann_root / "include"), str(probe_source),
            "-L", str(package / "lib"), "-L", str(cann_lib), "-L", str(driver_lib),
            "-Wl,-rpath-link," + str(package / "lib"), "-Wl,-rpath-link," + str(cann_lib),
            "-Wl,-rpath-link," + str(driver_lib), "-ltile-comm", "-lascendcl", "-lruntime",
            "-ldl", "-pthread", "-o", str(package / "bin/ccu_tilexr_direct_smoke_probe")],
           "probe-build", 120)
    record.update(probe_build="PASS", probe_source_sha256=digest(probe_source))
    (package / "REVISION").write_text(args["revision"] + "\n")
    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = str(package / "lib") + ":" + environment.get("LD_LIBRARY_PATH", "")
    check_bin = root / "aiv_prepare_regression"
    logged(["g++", "-std=c++14", "-I", str(source / "src/include"), "-I", str(source / "src/comm"),
            str(source / "debug_checks/aiv_prepare_regression.cpp"), "-L", str(package / "lib"),
            "-Wl,-rpath," + str(package / "lib"), "-ltile-comm", "-o", str(check_bin)], "host-check-build", 60)
    selected = subprocess.run(["ldd", str(check_bin)], env=environment, capture_output=True, text=True, timeout=15)
    require(selected.returncode == 0 and str(package / "lib/libtile-comm.so") in selected.stdout,
            "host_check_selected_wrong_core")
    cpu = subprocess.run([str(check_bin)], env=environment, capture_output=True, text=True, timeout=20)
    (root / "host-check.log").write_text(cpu.stdout + cpu.stderr)
    require(cpu.returncode == 0 and "4 Host cases passed" in cpu.stdout, "host_prepare_regression_failed")
    record["host_prepare_cases"] = 4
    for relative in ["lib/libtile-comm.so", "bin/ccu_tilexr_direct_smoke_probe",
                     "lib/libtilexr_ccu_aiv_fanout_kernel.so"]:
        linked = subprocess.run(["ldd", "-r", str(package / relative)], env=environment,
                                capture_output=True, text=True, timeout=25)
        text = linked.stdout + linked.stderr
        (root / (Path(relative).name + ".ldd.log")).write_text(text)
        require(linked.returncode == 0 and "not found" not in text and "undefined symbol:" not in text,
                "candidate_link_dependencies_unresolved")
        if relative == "bin/ccu_tilexr_direct_smoke_probe":
            require(str(package / "lib/libtile-comm.so") in text, "probe_selected_wrong_core")
    dynamic = subprocess.run(["readelf", "-d", str(package / "lib/libtile-comm.so")],
                             capture_output=True, text=True, timeout=10)
    require(dynamic.returncode == 0, "readelf_failed")
    require(not any("devlib" in line for line in dynamic.stdout.splitlines()
                    if "RPATH" in line or "RUNPATH" in line), "driver_stub_rpath")
    needed = "\n".join(line.lower() for line in dynamic.stdout.splitlines() if "NEEDED" in line)
    require(not any(name in needed for name in ["hccl", "hcomm", "shmem"]), "forbidden_core_dependency")
    files = {str(path.relative_to(package)): digest(path) for path in package.rglob("*")
             if path.is_file() and path != package / "deployment.json"}
    manifest = dict(previous, revision=args["revision"], source_directory=str(source),
                    build_directory=str(root), source_archive_sha256=args.get(
                        "retained_archive_sha256", args["source_archive_sha256"]),
                    baseline_revision=previous["revision"], files=files, npu_tests_run=False)
    (package / "deployment.json").write_text(json.dumps(manifest, indent=2) + "\n")
    rootinfo = Path(args["rootinfo"]).resolve(strict=True)
    require(rootinfo.is_relative_to(Path("/home/c00996840/ai/TileXR-deploy/config")), "invalid_rootinfo_location")
    require(digest(rootinfo) == args["rootinfo_sha256"], "rootinfo_hash_changed")
    root_content = json.loads(rootinfo.read_text())
    topology = Path(root_content["topo_file_path"]).resolve(strict=True)
    require(topology.parent == rootinfo.parent and digest(topology) == args["topology_sha256"], "topology_changed")
    devices = {int(item["device_id"]): int(item["local_id"]) for item in objects(root_content)
               if str(item.get("device_id")) in ["2", "3"] and "local_id" in item}
    require(set(devices) == {2, 3}, "requested_devices_absent_from_rootinfo")
    topo_content = json.loads(topology.read_text())
    routes = [item for item in objects(topo_content) if "local_a" in item and "local_b" in item
              and {int(item["local_a"]), int(item["local_b"])} == set(devices.values())
              and item.get("local_a_ports") and item.get("local_b_ports")]
    require(routes, "requested_devices_missing_topology_route")

    def probe(device):
        result = subprocess.run(["npu-smi", "info", "-t", "proc-mem", "-i", str(device)],
                                capture_output=True, text=True, timeout=12)
        state = "indeterminate"
        if result.returncode == 0:
            if re.search(r"Process id\s*:", result.stdout):
                state = "busy"
            elif re.search(r"NPU ID\s*:\s*" + str(device) + r"(?:\s|$)", result.stdout):
                state = "idle"
        health = subprocess.run(["npu-smi", "info", "-t", "health", "-i", str(device)],
                                capture_output=True, text=True, timeout=12)
        (root / ("npu-%d-preflight.log" % device)).write_text(result.stdout + result.stderr + health.stdout + health.stderr)
        match = re.search(r"Health Status\s*:\s*(\S+)", health.stdout)
        return {"device": device, "state": state, "health": match[1] if match else "indeterminate"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        record["devices"] = list(pool.map(probe, [2, 3]))
    record.update(status="PASS", revision=args["revision"], package=str(package), source_directory=str(source),
                  elf_and_link_dependencies="PASS", rootinfo=str(rootinfo),
                  rootinfo_sha256=args["rootinfo_sha256"], topology_sha256=args["topology_sha256"],
                  route_count=len(routes), ctp_route_count=sum("UB_CTP" in item.get("protocols", []) for item in routes),
                  runner_sha256=digest(source / "tests/ccu/run_tilexr_ccu_direct_smoke.sh"),
                  guard_sha256=digest(source / "tests/ccu/ccu_npu_smi_busy_guard.py"),
                  artifact_hashes={name: files[name] for name in ["lib/libtile-comm.so",
                      "bin/ccu_tilexr_direct_smoke_probe", "lib/libtilexr_ccu_aiv_fanout_kernel.so",
                      "lib/tilexr/kernels/ccu_tilexr_aiv_fanout_kernel.o"]}, npu_workload_run=False)


if __name__ == "__main__":
    settings = json.loads(base64.urlsafe_b64decode(sys.argv[1]).decode())
    summary = {"status": "FAIL", "npu_workload_run": False}
    try:
        run(settings, summary)
    except Exception as error:
        summary.update(error_type=type(error).__name__, reason=str(error)[:160])
    if "build_directory" in summary:
        (Path(summary["build_directory"]) / "debug-build-result.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("CZ_DEBUG_BUILD " + json.dumps(summary))
    sys.exit(0 if summary["status"] == "PASS" else 1)
