#!/bin/bash
set -euo pipefail
cd -- "$(dirname "$0")"
source /opt/sim/cann-env.sh
root="${ASCEND_HOME_PATH:-${CANN_ROOT:-}}"
arch="$root/$(uname -m)-linux"
compiler="$(command -v bisheng || command -v ccec || true)"
if [[ -z "$compiler" ]]; then
    for candidate in "$root/tools/bisheng_compiler/bin/bisheng" "$arch/ccec_compiler/bin/ccec"; do
        if [[ -x "$candidate" ]]; then compiler="$candidate"; break; fi
    done
fi
test -n "$compiler"
linker="${CCE_LINKER:-$(dirname "$compiler")/ld.lld}"
includes=(-Iinclude -Ishmem-device)
for base in "$root" "$arch"; do
    for path in include asc/include ascendc/include pkg_inc pkg_inc/runtime pkg_inc/profiling runtime/include compiler/tikcpp compiler/tikcpp/tikcfw compiler/tikcpp/tikcfw/impl compiler/tikcpp/tikcfw/interface tikcpp tikcpp/tikcfw tikcpp/tikcfw/impl tikcpp/tikcfw/interface; do
        if [[ -d "$base/$path" ]]; then includes+=("-I$base/$path"); fi
    done
done
set -x
"$compiler" --version
"$compiler" -xcce -c -O2 -std=gnu++17 --cce-aicore-only --cce-aicore-arch=dav-c310-vec \
    -DBACKEND_HYBM -DTILEXR_ENABLE_PROFILING=1 \
    -mllvm -cce-aicore-stack-size=0x8000 -mllvm -cce-aicore-function-stack-size=0x8000 \
    -mllvm -cce-aicore-record-overflow=true -mllvm -cce-aicore-addr-transform \
    -mllvm -cce-aicore-dcci-insert-for-scalar=false \
    "${includes[@]}" dispatch.profile.cpp -o dispatch.rel.o
"$linker" -m aicorelinux -Ttext=0 dispatch.rel.o --static --allow-multiple-definition -o dispatch.o
LC_ALL=C readelf -h -sW dispatch.o > dispatch.elf.txt
