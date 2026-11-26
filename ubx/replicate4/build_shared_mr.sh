#!/usr/bin/env bash
set -euo pipefail
base=/root/ubx-replicate4-e172cd2-bs32-f50
root="$base/runtime-shared-mr"
digest=$1
revision=$2
mode=${3:-build}
if [[ $mode == resume ]]; then
    [[ $(cat "$root/build-status.txt") == 'verify 1' ]]
    test ! -e "$root/runtime-result.json"
    test ! -e "$base/full-bs32-f50-shared-mr"
    exec 3>&1 >"$root/verify-resume.log" 2>&1
else
    exec 3>&1 >"$root/build.log" 2>&1
fi
phase=verify
trap 'code=$?; printf "%s %s\n" "$phase" "$code" > "$root/build-status.txt"' EXIT
if [[ $mode != resume ]]; then
phase=archive
printf '%s  %s\n' "$digest" "$root/source.tar.gz" | sha256sum --check --status
mkdir "$root/source"
tar -xzf "$root/source.tar.gz" -C "$root/source"
for dependency in picojson spdlog; do
    tar -xzf "$base/$dependency.tar.gz" -C "$root/source/3rdparty/$dependency"
done

phase=compile
set +u
source /usr/local/Ascend/ascend-toolkit/set_env.sh
set -u
cmake -S "$root/source" -B "$root/build" \
    -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DTILEXR_BUILD_TESTS=OFF \
    -DTILEXR_BUILD_UNITEST=OFF -DTILEXR_BUILD_RDMA=ON \
    -DCMAKE_INSTALL_PREFIX="$root/install"
cmake --build "$root/build" -j8
cmake --install "$root/build"
fi

phase=verify
python3 - "$root" "$revision" "$base" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
import tarfile

root, revision, base = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
baseline = json.loads((base / "build-result.json").read_text())
new, = (p for p in (root / "install").glob("lib*/libtile-comm.so") if p.is_file())
headers = base / "source/dispatch/dispatch-release/third_party/TileXR/include"
with tarfile.open(root / "source.tar.gz", "r:gz") as source:
    assert source.pax_headers["comment"] == revision
for name in ("tilexr_api.h", "comm_args.h", "tilexr_rdma.h", "tilexr_rdma_device_types.h"):
    expected = (headers / name).read_bytes().replace(b"\r\n", b"\n")
    installed = (root / "install/include" / name).read_bytes().replace(b"\r\n", b"\n")
    assert installed == expected, name
digest = hashlib.sha256(new.read_bytes()).hexdigest()
assert digest != baseline["runtime_sha256"]
(root / "runtime-result.json").write_text(json.dumps(dict(
    revision=revision, base_revision="0797410beab1da40800611affc9c512cd42525ba",
    runtime_sha256=digest,
)))
PY
phase=complete
cat "$root/runtime-result.json" >&3
