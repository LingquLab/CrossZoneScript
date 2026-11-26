#!/usr/bin/env bash
set -euo pipefail

root=/root/ubx-replicate4-e172cd2-bs32-f50
step=${1:-}
test ! -e "$root/build-result.json"
test ! -e "$root/full-bs32-f50"
if [[ $step == resume-dispatch ]]; then
    [[ $(cat "$root/build-status.txt") == 'build-dispatch 1' ]]
    test ! -e "$root/dispatch-configure-first.log"
    mv "$root/build.log" "$root/dispatch-configure-first.log"
    mv "$root/build-status.txt" "$root/dispatch-configure-first-status.txt"
else
    [[ $(cat "$root/build-status.txt") == 'fetch-dispatch 128' ]]
    test ! -e "$root/first-build-status.txt"
    mv "$root/build-status.txt" "$root/first-build-status.txt"
    mv "$root/build.log" "$root/first-build.log"
    if [[ -d "$root/source" ]]; then
        mv "$root/source" "$root/first-fetch"
    fi
fi
exec 3>&1 >"$root/build.log" 2>&1
phase=setup
trap 'code=$?; printf "%s %s\n" "$phase" "$code" > "$root/build-status.txt"; exit "$code"' EXIT

revision=e172cd211889e6da70e35da315771508a1a92c47
runtime_revision=0797410beab1da40800611affc9c512cd42525ba
clock_revision=3c7195b5fc8bc1584fcb05f891bc32d07e359dff
toolkit=/usr/local/Ascend/ascend-toolkit/set_env.sh
set +u
source "$toolkit"
set -u

if [[ $step != resume-dispatch ]]; then
phase=verify-staged-sources
printf '%s  %s\n' "$1" "$root/staged-input/sources.tar" | sha256sum --check --status
tar -xf "$root/staged-input/sources.tar" -C "$root"
for source_dir in source runtime-source clock-source; do
    mkdir -p "$root/$source_dir"
    tar -xzf "$root/$source_dir.tar.gz" -C "$root/$source_dir"
done
for source_dir in runtime-source clock-source; do
    tar -xzf "$root/spdlog.tar.gz" -C "$root/$source_dir/3rdparty/spdlog"
    tar -xzf "$root/picojson.tar.gz" -C "$root/$source_dir/3rdparty/picojson"
done
phase=build-runtime
cmake -S "$root/runtime-source" -B "$root/runtime-build" \
    -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DTILEXR_BUILD_TESTS=OFF \
    -DTILEXR_BUILD_UNITEST=OFF -DTILEXR_BUILD_RDMA=ON \
    -DCMAKE_INSTALL_PREFIX="$root/runtime-install"
cmake --build "$root/runtime-build" -j8
cmake --install "$root/runtime-build"

phase=build-clock
cmake -S "$root/clock-source" -B "$root/clock-build" \
    -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DTILEXR_BUILD_TESTS=OFF \
    -DTILEXR_BUILD_UNITEST=OFF -DTILEXR_BUILD_DIAGNOSTICS=ON \
    -DTILEXR_DIAGNOSTICS_SOC_TYPE=Ascend950PR \
    -DCMAKE_INSTALL_PREFIX="$root/clock-install"
cmake --build "$root/clock-build" -j8
cmake --install "$root/clock-build"
fi

phase=verify-clock-source
python3 - "$root" "$clock_revision" <<'PY'
from pathlib import Path
import sys
import tarfile

root = Path(sys.argv[1])
revision = sys.argv[2]
source = root / "clock-source"
with tarfile.open(root / "clock-source.tar.gz", "r:gz") as archive:
    assert archive.pax_headers["comment"] == revision
    native_kernel = "src/diagnostics/kernels/clock_sleep_at_kernel.cpp"
    assert archive.extractfile(native_kernel).read() == (source / native_kernel).read_bytes()
with (source / ".ubx-clock-revision").open("x") as marker:
    marker.write(revision + "\n")
PY

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
