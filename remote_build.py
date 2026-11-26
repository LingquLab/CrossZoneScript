from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import time

ROOT = Path(REQUEST["root"])
report = {"status": "BUILDING", "node": NODE, "stages": {}, "artifacts": {}}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def unpack(label, destination):
    raw, digest = REQUEST["archives"][label]
    data = base64.b64decode(raw, validate=True)
    assert sha(data) == digest
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            path = Path(member.name)
            assert not path.is_absolute() and ".." not in path.parts
            if member.issym():
                target = Path(member.linkname)
                assert not target.is_absolute() and ".." not in target.parts
            else:
                assert member.isdir() or member.isfile()
        archive.extractall(destination, members=members)


def step(label, argv, env, timeout):
    report["failure_stage"] = label
    start = time.monotonic()
    with (ROOT / (label + ".log")).open("wb") as log:
        done = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=timeout)
    report["stages"][label] = [done.returncode, round(time.monotonic() - start, 2)]
    if done.returncode:
        raise RuntimeError(label)


def build():
    ROOT.mkdir(parents=True, exist_ok=False)
    configured = Path(REQUEST["runtime"]["cann_env"])
    candidates = [configured, Path("/usr/local/Ascend/cann-9.3.0/set_env.sh")]
    env_file = next(path for path in candidates if path.is_file())
    out = subprocess.check_output(["bash", "-c", 'set -e; . "$1" >/dev/null 2>&1; env -0',
                                   "ubx-topk", str(env_file)], timeout=30)
    env = dict(row.decode().split("=", 1) for row in out.split(b"\0") if b"=" in row)
    cann = Path(env["ASCEND_HOME_PATH"])
    report["cann"] = cann.name
    arch = os.uname().machine
    compiler = next(path for path in (cann / "tools/bisheng_compiler/bin/ccec",
                    cann / (arch + "-linux/ccec_compiler/bin/ccec"),
                    Path(shutil.which("bisheng", path=env.get("PATH")) or "/nonexistent")) if path.is_file())
    linker = compiler.with_name("ld.lld")
    assert linker.is_file()

    def probe(device):
        q = subprocess.run(["npu-smi", "info", "-t", "proc-mem", "-i", str(device)],
                           capture_output=True, text=True, timeout=15)
        if q.returncode or not re.search(r"NPU ID\s*:\s*" + str(device) + r"(?:\s|$)", q.stdout):
            return device, "indeterminate"
        if re.search(r"Process id\s*:", q.stdout, re.I):
            return device, "busy"
        return device, "idle" if "No process in device." in q.stdout else "indeterminate"

    with ThreadPoolExecutor(max_workers=8) as pool:
        observed = dict(pool.map(probe, REQUEST["devices"][NODE]))
    for status in ("idle", "busy", "indeterminate"):
        report[status] = [device for device, value in observed.items() if value == status]

    for variant in REQUEST["revisions"]:
        source = ROOT / (variant + "-source")
        unpack(variant, source)
        for dependency in ("spdlog", "picojson"):
            unpack(dependency, source / "3rdparty" / dependency)
        if variant == "baseline":
            ops = source / "src/ep/kernels/tilexr_ep_ubx_route_ops.h"
            original = ops.read_text()
            qualified = original.replace("            ClearSpr<SpecialPurposeReg::AR>();",
                                         "            MicroAPI::ClearSpr<SpecialPurposeReg::AR>();", 1)
            qualified = qualified.replace("Squeeze<uint32_t, GatherMaskMode::STORE_REG>(packedEnds",
                                          "Squeeze<uint32_t, MicroAPI::GatherMaskMode::STORE_REG>(packedEnds", 1)
            assert qualified != original
            ops.write_text(qualified)
            (ROOT / "baseline-overlay.json").write_text(json.dumps(
                {"kind": "qualify two SIMD names for CANN", "before": sha(original.encode()),
                 "after": sha(qualified.encode())}))
        for mode in ("off", "on"):
            label = variant + "-" + mode
            package = ROOT / label
            build_dir = ROOT / (label + "-build")
            core = [f"-DASCEND_HOME_PATH={cann}", "-DASCEND_DRIVER_PATH=/usr/local/Ascend/driver",
                    f"-DCMAKE_CCE_COMPILER={compiler}", f"-DCMAKE_CCE_LINKER={linker}",
                    f"-DCMAKE_INSTALL_PREFIX={package}", "-DCMAKE_INSTALL_LIBDIR=lib",
                    "-DCMAKE_BUILD_TYPE=Release"]
            step(label + "-configure", ["cmake", "-S", str(source), "-B", str(build_dir)] + core + [
                "-DTILEXR_BUILD_EP=ON", "-DTILEXR_BUILD_DIAGNOSTICS=ON", "-DTILEXR_BUILD_UNITEST=OFF",
                "-DBUILD_TESTING=OFF", "-DTILEXR_BUILD_TESTS=OFF", "-DTILEXR_EP_UBX_PACKET_TRACE=OFF",
                "-DTILEXR_DIAGNOSTICS_SOC_TYPE=Ascend950", "-DTILEXR_EP_ENABLE_PROFILING=" + mode.upper()], env, 90)
            step(label + "-build", ["cmake", "--build", str(build_dir), "-j", "8"], env, 330)
            step(label + "-install", ["cmake", "--install", str(build_dir)], env, 30)
            fixture = ROOT / (label + "-fixture")
            step(label + "-fixture-configure", ["cmake", "-S", str(source / "tests/ep/ubx_full_intra"),
                 "-B", str(fixture)] + core + [f"-DTILEXR_HOST_PREFIX={package}",
                 "-DTILEXR_FIXTURE_BARRIER_PROFILING=ON"], env, 90)
            step(label + "-fixture-build", ["cmake", "--build", str(fixture), "-j", "6"], env, 140)
            step(label + "-fixture-install", ["cmake", "--install", str(fixture)], env, 30)
            paths = ["bin/ubx_full_intra", "lib/libtilexr-ep.so", "lib/libtile-comm.so",
                     "lib/libtilexr-diagnostics.so", "lib/libubx_fixture_barrier.so",
                     "lib/tilexr/kernels/tilexr_ep_ubx_kernel.o"]
            report["artifacts"][label] = {path: sha((package / path).read_bytes()) for path in paths}
    report.pop("failure_stage", None)
    report["status"] = "PASS"


try:
    build()
except Exception as error:
    report.update(status="FAILED", error_type=type(error).__name__)
if ROOT.is_dir():
    (ROOT / "build-receipt.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, separators=(",", ":")))
