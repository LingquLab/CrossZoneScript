#!/usr/bin/env bash
set -euo pipefail

revision=$1
variant=${2:-two-group}
image=sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5
run_dir=$(mktemp -d /opt/urma64p-full.XXXXXX)
tar -xzf - -C "$run_dir"
printf '{"stage":"staged","revision":"%s","run_dir":"%s"}\n' "$revision" "$run_dir"

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
  -DTILEXR_ALLTOALL_A5_SIM_SPLIT_SQ_FETCH=1
  -I"$cann/compiler/tikcpp" -I"$cann/compiler/tikcpp/tikcfw"
  -I"$cann/compiler/tikcpp/tikcfw/impl" -I"$cann/compiler/tikcpp/tikcfw/interface"
  -I"$cann/aarch64-linux/ascendc/include" -I"$cann/aarch64-linux/include"
  -I"$cann/aarch64-linux/pkg_inc" -I"$cann/aarch64-linux/pkg_inc/runtime"
  -Iinclude -Isrc/include -Isrc/collectives/kernels)
set -x
if [[ "$1" != "two-group" ]]; then
  schedules=("$1")
  if [[ "$1" == "clos-4plus8" ]]; then
    compile+=(-DTILEXR_JETTY_PEERS_CLOS_4PLUS8=1)
  else
    compile+=(-DTILEXR_JETTY_PEERS_CLOS_8PLUS8_D2=1)
  fi
  mode=-1
else
  schedules=(syncall clock nosync)
  compile+=(-DTILEXR_ALLTOALL_A5_SIM_SPLIT_UDMA_WRITES=1)
  mode=0
fi
for schedule in "${schedules[@]}"; do
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
' -- "$variant" > "$run_dir/build.log" 2>&1; then
    printf '{"stage":"compile","passed":false,"log":"%s/build.log"}\n' "$run_dir"
    exit 1
fi

python3 - "$run_dir" "$revision" "$variant" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

directory = Path(sys.argv[1])
objects = {
    str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(directory.glob("*/*/tilexr_alltoall_urma_jetty_peers_full_kernel.o"))
}
assert len(objects) == (6 if sys.argv[3] == "two-group" else 2)
summary = {
    "stage": "compile", "revision": sys.argv[2], "passed": True,
    "run_dir": str(directory), "compiler": (directory / "compiler.txt").read_text().strip(),
    "objects": objects,
}
(directory / "objects.json").write_text(json.dumps(summary) + "\n")
print(json.dumps(summary))
PY
