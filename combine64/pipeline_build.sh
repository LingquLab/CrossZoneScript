#!/usr/bin/env bash
# Copyright (c) 2026 TileXR Project.
# Compile the joint SST entry against the fixed, read-only candidate stage sources.
set -eo pipefail
source /opt/sim/cann-env.sh
export ASCEND_HOME_PATH=/opt/sim/Ascend/cann-9.1.0
export PATH="$ASCEND_HOME_PATH/tools/bisheng_compiler/bin:$ASCEND_HOME_PATH/aarch64-linux/ccec_compiler/bin:$ASCEND_HOME_PATH/aarch64-linux/bin:$ASCEND_HOME_PATH/bin:$PATH"
target=/work/pipeline-build-40596e2-a1
mkdir "$target"
cp "$1" "$target/pipeline_kernel.cpp"
includes=("-I$(dirname "$1")" -I/work/source/include -I/work/source/src/moonep/combine_ht/common
          -I/work/source/src/moonep/combine_ht/kernels -I/work/source/src/ep/combine_ht)
for path in compiler/tikcpp compiler/tikcpp/tikcfw compiler/tikcpp/tikcfw/impl compiler/tikcpp/tikcfw/interface \
            aarch64-linux/ascendc/include aarch64-linux/asc/include aarch64-linux/include aarch64-linux/pkg_inc aarch64-linux/pkg_inc/runtime; do
    includes+=("-I$ASCEND_HOME_PATH/$path")
done
set +e
bisheng -xcce -O2 -DCATLASS_ARCH=3510 --cce-auto-sync -Wno-deprecated-declarations \
    --cce-aicore-arch=dav-c310-vec -mllvm -cce-aicore-stack-size=0x8000 \
    -mllvm -cce-aicore-function-stack-size=0x8000 -mllvm -cce-aicore-addr-transform \
    -mllvm -cce-aicore-dcci-insert-for-scalar=false \
    -DTILEXR_ENABLE_PROFILING=1 -DTILEXR_COMBINE_HT_SIM_SEND=1 -DTILEXR_COMBINE_HT_SST_FP32=1 \
    -DTILEXR_EP_HT_SST_SPLIT_WRITES=1 -DTILEXR_EP_HT_FINAL_REDUCE_SST_FP32=1 \
    -DTILEXR_EP_HT_PIPELINE_FP32=1 --cce-aicore-only -std=gnu++17 \
    "${includes[@]}" -c "$target/pipeline_kernel.cpp" -o "$target/rel.o" > "$target/compile.log" 2>&1
compiled=$?
linked=99
if [[ "$compiled" -eq 0 ]]; then
    ld.lld -m aicorelinux -Ttext=0 "$target/rel.o" --static --allow-multiple-definition \
        -o "$target/kernel.o" > "$target/link.log" 2>&1
    linked=$?
fi
set -e
python3 - "$target" "$compiled" "$linked" <<'PY'
import hashlib,json,re,sys
from pathlib import Path
root=Path(sys.argv[1])
result={'compile_exit':int(sys.argv[2]),'link_exit':int(sys.argv[3]),'entry':'tilexr_ht_pipeline_sst',
        'candidate_commit':'40596e2d9d454b7a7b00b4b34b96d49158e1c3a3','test_wrapper':True,'abi_bytes':512}
for name in ('compile','link'):
    p=root/(name+'.log')
    if p.is_file():
        errors=[line[:250] for line in p.read_text(errors='replace').splitlines() if re.search(r'error:|Error:',line)]
        if errors:result[name+'_errors']=errors[:4]
p=root/'kernel.o'
if p.is_file():result.update(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
(root/'build-result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,separators=(',',':')))
raise SystemExit(0 if result['compile_exit']==result['link_exit']==0 and p.is_file() else 1)
PY
