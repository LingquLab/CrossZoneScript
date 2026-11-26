#!/usr/bin/env bash
set -euo pipefail
base=/root/ubx-replicate4-e172cd2-bs32-f50
root="$base/runtime-shared-mr"
digest=$1
revision=$2
exec 3>&1 >"$root/build.log" 2>&1
phase=archive
trap 'code=$?; printf "%s %s\n" "$phase" "$code" > "$root/build-status.txt"' EXIT
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

phase=verify
python3 - "$root" "$revision" "$base" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

root, revision, base = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
baseline = json.loads((base / "build-result.json").read_text())
new, = (p for p in (root / "install").glob("lib*/libtile-comm.so") if p.is_file())
headers = base / "source/dispatch/dispatch-release/third_party/TileXR/include"
for name in ("tilexr_api.h", "comm_args.h", "tilexr_rdma.h", "tilexr_rdma_device_types.h"):
    expected = (headers / name).read_bytes().replace(b"\r\n", b"\n")
    assert (root / "install/include" / name).read_bytes() == expected, name
digest = hashlib.sha256(new.read_bytes()).hexdigest()
assert digest != baseline["runtime_sha256"]
(root / "runtime-result.json").write_text(json.dumps(dict(
    revision=revision, base_revision="0797410beab1da40800611affc9c512cd42525ba",
    runtime_sha256=digest,
)))
PY
phase=complete
cat "$root/runtime-result.json" >&3
