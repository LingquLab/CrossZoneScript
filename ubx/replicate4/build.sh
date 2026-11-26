#!/usr/bin/env bash
set -euo pipefail

root=/root/ubx-replicate4-e172cd2-bs32-f50
mkdir "$root"
exec 3>&1 >"$root/build.log" 2>&1
phase=setup
trap 'code=$?; printf "%s %s\n" "$phase" "$code" > "$root/build-status.txt"; exit "$code"' EXIT

revision=e172cd211889e6da70e35da315771508a1a92c47
runtime_revision=0797410beab1da40800611affc9c512cd42525ba
clock_revision=3c7195b5fc8bc1584fcb05f891bc32d07e359dff
origin=https://github.com/LingquLab/TileXR.git
toolkit=/usr/local/Ascend/ascend-toolkit/set_env.sh
set +u
source "$toolkit"
set -u

checkout() {
    local dir=$1 expected=$2
    git init -q "$dir"
    git -C "$dir" remote add origin "$origin"
    GIT_TERMINAL_PROMPT=0 timeout 150 git -C "$dir" -c http.https://github.com/.sslVerify=false \
        fetch --no-tags --depth=1 origin "$expected"
    git -C "$dir" checkout -q --detach FETCH_HEAD
    test "$(git -C "$dir" rev-parse HEAD)" = "$expected"
}

fetch_dependencies() {
    local dir=$1 expected=$2
    checkout "$dir" "$expected"
    GIT_TERMINAL_PROMPT=0 timeout 150 git -C "$dir" -c http.https://github.com/.sslVerify=false \
        submodule update --init --depth=1 3rdparty/spdlog 3rdparty/picojson
}

phase=fetch-dispatch
checkout "$root/source" "$revision"
phase=fetch-runtime
fetch_dependencies "$root/runtime-source" "$runtime_revision"
phase=build-runtime
cmake -S "$root/runtime-source" -B "$root/runtime-build" \
    -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DTILEXR_BUILD_TESTS=OFF \
    -DTILEXR_BUILD_UNITEST=OFF -DTILEXR_BUILD_RDMA=ON \
    -DCMAKE_INSTALL_PREFIX="$root/runtime-install"
cmake --build "$root/runtime-build" -j8
cmake --install "$root/runtime-build"

phase=fetch-clock
fetch_dependencies "$root/clock-source" "$clock_revision"
phase=build-clock
cmake -S "$root/clock-source" -B "$root/clock-build" \
    -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DTILEXR_BUILD_TESTS=OFF \
    -DTILEXR_BUILD_UNITEST=OFF -DTILEXR_BUILD_DIAGNOSTICS=ON \
    -DTILEXR_DIAGNOSTICS_SOC_TYPE=Ascend950PR \
    -DCMAKE_INSTALL_PREFIX="$root/clock-install"
cmake --build "$root/clock-build" -j8
cmake --install "$root/clock-build"

phase=build-dispatch
release="$root/source/dispatch/dispatch-release"
cmake -S "$release" -B "$root/dispatch-build" \
    -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DUBX_BUILD_HARDWARE_TESTS=ON \
    -DUBX_BUILD_CLOCK_CALIBRATOR=ON -DUBX_RDMA_DEBUG=OFF \
    -DTILEXR_INSTALL="$root/runtime-install" -DUBX_CLOCK_TILEXR_SOURCE="$root/clock-source" \
    -DUBX_CLOCK_TILEXR_INSTALL="$root/clock-install"
cmake --build "$root/dispatch-build" -j8

phase=verify
python3 - "$root" "$revision" "$runtime_revision" "$clock_revision" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
revision, runtime_revision, clock_revision = sys.argv[2:]
release = root / "source/dispatch/dispatch-release"
build = root / "dispatch-build"
names = (
    "ubx_dispatch_ep32.o", "libubx_dispatch_host.so", "ubx_dispatch_hardware_ep32",
    "ubx_dispatch_round_barrier.o", "ubx_clock_calibrate", "ubx_clock_wait.o",
)
headers = (
    "tilexr_api.h", "comm_args.h", "tilexr_rdma.h", "tilexr_sdma_types.h",
    "tilexr_rdma_device_types.h", "tilexr_udma_fullmesh.h",
)
for name in headers:
    header = (release / "third_party/TileXR/include" / name).read_bytes()
    installed = (root / "runtime-install/include" / name).read_bytes()
    assert header.replace(b"\r\n", b"\n") == installed.replace(b"\r\n", b"\n"), name

fixture = release / "testcase/ep32-full"
manifest = json.loads((fixture / "manifest.json").read_text())
assert manifest["ep_size"] == 32 and manifest["mode"] == "full"
case, = (case for case in manifest["cases"] if case["bs"] == 32)
for entry in case["files"].values():
    data = (fixture / entry["file"]).read_bytes()
    assert len(data) == entry["bytes"], entry["file"]
    assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["file"]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

library, = (path for path in (root / "runtime-install").glob("lib*/libtile-comm.so") if path.is_file())
receipt = dict(
    built=True, revision=revision, runtime_revision=runtime_revision, clock_revision=clock_revision,
    runtime_sha256=digest(library), artifacts={name: digest(build / name) for name in names},
    fixture_sha256=digest(fixture / "manifest.json"), npu_workload_started=False,
)
(root / "build-result.json").write_text(json.dumps(receipt))
PY
phase=complete
cat "$root/build-result.json" >&3
