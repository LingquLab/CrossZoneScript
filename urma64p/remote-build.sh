#!/usr/bin/env bash
set -euo pipefail

revision=$1
image=sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5
run_dir=$(mktemp -d /opt/urma64p-full.XXXXXX)
tar -xzf - -C "$run_dir"
printf '{"stage":"staged","revision":"%s","run_dir":"%s"}\n' "$revision" "$run_dir"

python3 - "$image" <<'PY'
import json
import os
import re
import subprocess
import sys

result = subprocess.run(
    ["docker", "ps", "-a", "--filter", f"ancestor={sys.argv[1]}", "-q"],
    check=True, text=True, capture_output=True, timeout=10,
)
roots = {"/home", "/root", "/opt", "/work", "/data", "/mnt"}
for container in result.stdout.splitlines()[:40]:
    record = subprocess.run(
        ["docker", "inspect", "--format", "{{json .Mounts}}", container],
        check=True, text=True, capture_output=True, timeout=10,
    )
    roots.update(m["Source"] for m in json.loads(record.stdout) if m["Type"] == "bind")
campaigns = set()
for base in sorted(roots):
    if base == "/" or not os.path.isdir(base):
        continue
    root_depth = base.rstrip("/").count("/")
    for root, dirs, files in os.walk(base):
        if re.search(r"full-(?:performance-matrix|finalcq)|full.*80cc17", os.path.basename(root)):
            campaigns.add(root)
            dirs[:] = []
            continue
        if root.count("/") - root_depth >= 4 or len(campaigns) >= 16:
            dirs[:] = []
        else:
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {
                "overlay2", "docker", "Ascend", "node_modules", "site-packages", "lost+found",
            }]
print(json.dumps({"stage": "case_discovery", "campaigns": sorted(campaigns)[:16]}))
PY

if ! docker run --rm --network none -v "$run_dir:/work" -w /work \
    --entrypoint /bin/bash "$image" -lc '
set -eo pipefail
cann=/opt/sim/Ascend/cann-9.1.0
source "$cann/set_env.sh"
bisheng --version | head -1 > compiler.txt
linker=$(command -v ld.lld || command -v cce-ld)
kernel=tilexr_alltoall_urma_jetty_peers_full_kernel
compile=(-xcce -O2 -DCATLASS_ARCH=3510 --cce-aicore-arch=dav-c310-vec --cce-aicore-only -std=gnu++17
  -mllvm -cce-aicore-stack-size=0x8000 -mllvm -cce-aicore-function-stack-size=0x8000
  -mllvm -cce-aicore-record-overflow=true -mllvm -cce-aicore-addr-transform
  -mllvm -cce-aicore-dcci-insert-for-scalar=false
  -DTILEXR_GROUPED_KERNEL_VARIANT_64=1 -DTILEXR_ENABLE_PROFILING=1
  -DTILEXR_ALLTOALL_A5_SIM_MTE_COPY=1 -DTILEXR_ALLTOALL_A5_SIM_MTE_QUEUE_STATE=1
  -DTILEXR_ALLTOALL_A5_SIM_SPLIT_UDMA_WRITES=1 -DTILEXR_ALLTOALL_A5_SIM_SPLIT_SQ_FETCH=1
  -I"$cann/compiler/tikcpp" -I"$cann/compiler/tikcpp/tikcfw"
  -I"$cann/compiler/tikcpp/tikcfw/impl" -I"$cann/compiler/tikcpp/tikcfw/interface"
  -I"$cann/aarch64-linux/ascendc/include" -I"$cann/aarch64-linux/include"
  -I"$cann/aarch64-linux/pkg_inc" -I"$cann/aarch64-linux/pkg_inc/runtime"
  -Isrc/include -Isrc/collectives/kernels)
set -x
mode=0
for schedule in syncall clock nosync; do
  mode=$((mode + 1))
  for kind in functional performance; do
    copyout=0
    if [[ "$kind" == functional ]]; then copyout=1; fi
    output="$schedule/$kind"
    mkdir -p "$output"
    bisheng "${compile[@]}" -DTILEXR_JETTY_PEERS_GROUP_SYNC="$mode" -DTILEXR_ALLTOALL_COPYOUT="$copyout" \
      -c "src/collectives/kernels/$kernel.cpp" -o "$output/rel.o"
    "$linker" -m aicorelinux -Ttext=0 "$output/rel.o" --static --allow-multiple-definition -o "$output/$kernel.o"
  done
done
' > "$run_dir/build.log" 2>&1; then
    printf '{"stage":"compile","passed":false,"log":"%s/build.log"}\n' "$run_dir"
    exit 1
fi

python3 - "$run_dir" "$revision" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

directory = Path(sys.argv[1])
objects = {
    str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(directory.glob("*/*/tilexr_alltoall_urma_jetty_peers_full_kernel.o"))
}
assert len(objects) == 6
summary = {
    "stage": "compile", "revision": sys.argv[2], "passed": True,
    "run_dir": str(directory), "compiler": (directory / "compiler.txt").read_text().strip(),
    "objects": objects,
}
(directory / "objects.json").write_text(json.dumps(summary) + "\n")
print(json.dumps(summary))
PY
