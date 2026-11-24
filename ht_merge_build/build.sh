#!/usr/bin/env bash
# Compile only the four HT stages and their independent Host libraries.
set -e
cd /work
source /opt/sim/cann-env.sh
export ASCEND_HOME_PATH=/opt/sim/Ascend/cann-9.1.0
toolkit_bin="$ASCEND_HOME_PATH/tools/bisheng_compiler/bin"
export PATH="$toolkit_bin:$ASCEND_HOME_PATH/aarch64-linux/ccec_compiler/bin:$ASCEND_HOME_PATH/aarch64-linux/bin:$ASCEND_HOME_PATH/bin:$PATH"
mkdir -p logs
set +e
native=(tilexr_moonep_combine_ht_kernel tilexr_ep_ht_inter_server_send_kernel tilexr_ep_ht_final_reduce_kernel)
host=(tilexr-moonep-combine-ht tilexr-ep-ht-send tilexr-ep-ht-final-reduce)
targets=( "${native[@]}" "${host[@]}" )
common=(-DTILEXR_BUILD_EP=ON -DTILEXR_BUILD_COMBINE_HT=ON -DTILEXR_BUILD_UNITEST=OFF
        -DTILEXR_BUILD_TESTS=OFF -DBUILD_TESTING=OFF -DTILEXR_DIAGNOSTICS_SOC_TYPE=Ascend950)
if [[ -d "$ASCEND_HOME_PATH/aarch64-linux/devlib" ]]; then
    common+=("-DCMAKE_SHARED_LINKER_FLAGS=-L$ASCEND_HOME_PATH/aarch64-linux/devlib")
fi
run() {
    local label="$1"
    shift
    "$@" > "logs/$label.log" 2>&1
    local status=$?
    printf '%s %d\n' "$label" "$status" >> logs/exit-status
    return "$status"
}
run native-config cmake -S source -B native "${common[@]}" &&
    for target in "${targets[@]}"; do
        run "native-$target" cmake --build native --target "$target" -j 8
    done
run sst-config cmake -S source -B sst "${common[@]}" \
    -DTILEXR_COMBINE_HT_SIM_SEND=ON -DTILEXR_COMBINE_HT_SST_FP32=ON &&
    for target in tilexr_moonep_combine_ht_kernel tilexr_ep_ht_final_reduce_sst_kernel; do
        run "sst-$target" cmake --build sst --target "$target" -j 8
    done
python3 - <<'PY'
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil

files = {
    "native-tilexr_moonep_combine_ht_kernel": "native/src/moonep/combine_ht/tilexr_moonep_combine_ht_kernel.o",
    "native-tilexr_ep_ht_inter_server_send_kernel": "native/src/ep/tilexr_ep_ht_inter_server_send_kernel.o",
    "native-tilexr_ep_ht_final_reduce_kernel": "native/src/ep/tilexr_ep_ht_final_reduce_kernel.o",
    "sst-tilexr_moonep_combine_ht_kernel": "sst/src/moonep/combine_ht/tilexr_moonep_combine_ht_kernel.o",
    "sst-tilexr_ep_ht_final_reduce_sst_kernel": "sst/src/ep/tilexr_ep_ht_final_reduce_sst_kernel.o",
    "native-tilexr-moonep-combine-ht": "native/src/moonep/combine_ht/libtilexr-moonep-combine-ht.so",
    "native-tilexr-ep-ht-send": "native/src/ep/libtilexr-ep-ht-send.so",
    "native-tilexr-ep-ht-final-reduce": "native/src/ep/libtilexr-ep-ht-final-reduce.so",
}
codes = {name: int(code) for name, code in (line.split() for line in Path("logs/exit-status").read_text().splitlines())}
errors = {
    "compiler_not_found": r"Could not find BISHENG|CMAKE_CCE_COMPILER.*NOTFOUND|command not found",
    "header_missing": r"fatal error:.*(?:not found|No such file)",
    "hal_missing": r"cannot find -lascend_hal",
    "unresolved_symbol": r"undefined reference",
    "unsupported_option": r"unknown argument|unsupported option",
    "cmake_failure": r"CMake Error",
}
results = {}
for name in [*("native-config", "sst-config"), *files]:
    item = {"exit": codes.get(name)}
    if item["exit"] == 0 and name in files:
        artifact = Path(files[name])
        if artifact.is_file():
            item.update(bytes=artifact.stat().st_size, sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
        else:
            item["artifact_missing"] = True
    if item["exit"]:
        log = Path(f"logs/{name}.log").read_text(errors="replace")
        item["error_categories"] = [key for key, pattern in errors.items() if re.search(pattern, log)]
    results[name] = item
passed = all(value["exit"] == 0 and "artifact_missing" not in value for value in results.values())
result = {"status": "compiled" if passed else "incomplete", "arch": platform.machine(),
          "cann": "9.1.0", "toolchain": {cmd: bool(shutil.which(cmd)) for cmd in ("cmake", "bisheng", "ccec", "ld.lld")},
          "targets": results}
Path("build-summary.json").write_text(json.dumps(result, indent=2))
PY
