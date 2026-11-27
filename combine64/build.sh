#!/usr/bin/env bash
# Copyright (c) 2026 TileXR Project.
# Compile the pinned HT stage binaries and retain per-target build evidence.
set -eo pipefail
cd /work
source /opt/sim/cann-env.sh
export ASCEND_HOME_PATH=/opt/sim/Ascend/cann-9.1.0
export PATH="$ASCEND_HOME_PATH/tools/bisheng_compiler/bin:$ASCEND_HOME_PATH/aarch64-linux/ccec_compiler/bin:$ASCEND_HOME_PATH/aarch64-linux/bin:$ASCEND_HOME_PATH/bin:$PATH"
mkdir -p logs
python3 - <<'PY'
import json,platform,shutil,subprocess
from pathlib import Path
p=subprocess.run(['bisheng','--version'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
Path('compiler-version.txt').write_bytes(p.stdout)
Path('toolchain.json').write_text(json.dumps({'arch':platform.machine(),'bisheng':shutil.which('bisheng'),'cann':'9.1.0'}))
PY
common=(-DTILEXR_BUILD_EP=OFF -DTILEXR_BUILD_COMBINE_HT=ON -DTILEXR_BUILD_UNITEST=OFF
        -DTILEXR_BUILD_TESTS=OFF -DBUILD_TESTING=OFF -DTILEXR_ENABLE_PROFILING=OFF
        -DTILEXR_COMBINE_HT_SIM_SEND=ON -DTILEXR_EP_HT_SST_SPLIT_WRITES=ON
        -DTILEXR_DIAGNOSTICS_SOC_TYPE=Ascend950)
targets=(tilexr_moonep_combine_ht_kernel tilexr_ep_ht_local_return_kernel
         tilexr_ep_ht_inter_server_send_kernel tilexr_ep_ht_final_reduce_kernel
         tilexr_ht_pipeline_sst_kernel)
for variant in fp32; do
    fp=ON
    set +e
    cmake -S /work/source -B "/work/build-$variant" "${common[@]}" \
        "-DTILEXR_COMBINE_HT_SST_FP32=$fp" > "logs/$variant-config.log" 2>&1
    configured=$?
    set -e
    printf '%s %d\n' "$variant-config" "$configured" >> logs/exit-status
    [[ "$configured" -eq 0 ]] || continue
    for target in "${targets[@]}"; do
        python3 - "$variant" "$target" <<'PY'
import json,sys,time
from pathlib import Path
Path('progress.json').write_text(json.dumps({'stage':'compile','variant':sys.argv[1],'target':sys.argv[2],'updated_unix':time.time()}))
PY
        set +e
        cmake --build "/work/build-$variant" --target "$target" -j 8 > "logs/$variant-$target.log" 2>&1
        code=$?
        set -e
        printf '%s %d\n' "$variant-$target" "$code" >> logs/exit-status
    done
done
python3 - <<'PY'
import hashlib,json,re,time
from pathlib import Path
codes={name:int(code) for name,code in (line.split() for line in Path('logs/exit-status').read_text().splitlines())}
results={}
for name,code in codes.items():
    row={'exit_code':code}
    if code:
        log=Path('logs/'+name+'.log').read_text(errors='replace')
        row['errors']=[line.strip()[:220] for line in log.splitlines() if re.search(r'(?:fatal error:|error:|CMake Error|Could NOT find)',line)][:4]
    results[name]=row
for variant in ('fp32',):
    root=Path('build-'+variant)
    for target,relative in (
        ('tilexr_moonep_combine_ht_kernel','src/moonep/combine_ht'),
        ('tilexr_ep_ht_local_return_kernel','src/ep/combine_ht'),
        ('tilexr_ep_ht_inter_server_send_kernel','src/ep/combine_ht'),
        ('tilexr_ep_ht_final_reduce_kernel','src/ep/combine_ht'),
        ('tilexr_ht_pipeline_sst_kernel','src/ep/combine_ht')):
        row=results.setdefault(variant+'-'+target,{'exit_code':None})
        p=root/relative/(target+'.o')
        if p.is_file(): row.update(path=str(p),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
passed=len(results)==6 and all(row['exit_code']==0 for row in results.values())
summary={'all_compiled':passed,'sim_send':True,'sst_split_writes':True,'profiling':False,'targets':results}
Path('build-summary.json').write_text(json.dumps(summary,indent=2))
Path('progress.json').write_text(json.dumps({'stage':'compiled' if passed else 'compile_failed','updated_unix':time.time()}))
raise SystemExit(0 if passed else 1)
PY
