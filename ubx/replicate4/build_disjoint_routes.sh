#!/usr/bin/env bash
set -euo pipefail
root="$1"
baseline="$2"
sha="$3"
revision="$4"
exec 3>&1 >"$root/build.log" 2>&1
phase=archive
trap 'code=$?; printf "%s %s\n" "$phase" "$code" > "$root/build-status.txt"' EXIT
printf '%s  %s\n' "$sha" "$root/source.tar.gz" | sha256sum --check --status
mkdir "$root/source"
tar -xzf "$root/source.tar.gz" -C "$root/source"
ln -s "$baseline/clock-install" "$root/clock-install"

phase=compile
set +u
source /usr/local/Ascend/ascend-toolkit/set_env.sh
set -u
cmake -S "$root/source/dispatch/dispatch-release" -B "$root/dispatch-build" \
    -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DUBX_BUILD_HARDWARE_TESTS=ON \
    -DUBX_BUILD_CLOCK_CALIBRATOR=ON -DUBX_RDMA_DEBUG=OFF \
    -DTILEXR_INSTALL="$baseline/runtime-install" -DUBX_CLOCK_TILEXR_SOURCE="$baseline/clock-source" \
    -DUBX_CLOCK_TILEXR_INSTALL="$baseline/clock-install"
cmake --build "$root/dispatch-build" -j8

phase=verify
python3 - "$root" "$baseline" "$revision" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
import tarfile

root, previous, revision = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
with tarfile.open(root / "source.tar.gz", "r:gz") as archive:
    assert archive.pax_headers["comment"] == revision
original = json.loads((previous / "build-result.json").read_text())
def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()
result = dict(
    built=True, revision=revision, runtime_revision=original["runtime_revision"],
    runtime_sha256=original["runtime_sha256"],
    clock_revision=original["clock_revision"],
    artifacts={name: digest(root / "dispatch-build" / name) for name in original["artifacts"]},
)
assert result["artifacts"]["ubx_dispatch_ep32.o"] != original["artifacts"]["ubx_dispatch_ep32.o"]
manifest = root / "source/dispatch/dispatch-release/testcase/ep32-full/manifest.json"
assert digest(manifest) == original["fixture_sha256"]
(root / "build-result.json").write_text(json.dumps(result))
PY
phase=complete
cat "$root/build-result.json" >&3
