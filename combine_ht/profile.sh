#!/usr/bin/env bash
# Build the same pinned sources with profiling off and on, then run each benchmark mode.
set -eo pipefail
export LC_ALL=C
umask 077
revision=$1
digest=$2
idle_timeout=$3
root="$HOME/.cache/tilexr-ht-be110cc8"
run="$root/profile-${revision:0:12}"
source_dir="$root/src-$revision"
archive="$root/tilexr-$revision.tar.gz"
mkdir -p "$source_dir"
printf '%s  %s\n' "$digest" "$archive" | sha256sum -c
tar -xzf "$archive" -C "$source_dir"
cd "$source_dir"
source /usr/local/Ascend/cann/set_env.sh >"$run/environment.log" 2>&1
source scripts/common_env.sh >>"$run/environment.log" 2>&1
unset ASCEND_RT_VISIBLE_DEVICES
export TILEXR_UDMA_ROOTINFO_PATH="$root/udma-rootinfo.json"

build() {
    local flag=$1 dir="$run/build-$1"
    cmake -S . -B "$dir" -DCMAKE_BUILD_TYPE=Release -DTILEXR_BUILD_COMBINE_HT=ON \
        -DTILEXR_COMBINE_HT_SIM_SEND=OFF -DTILEXR_BUILD_UNITEST=OFF \
        -DTILEXR_ENABLE_PROFILING="$flag" -DBUILD_TESTING=ON &&
    timeout 600 cmake --build "$dir" --target tilexr_combine_ht_test -j8 &&
    ctest --test-dir "$dir" -R '^tilexr_combine_ht_contract$' --output-on-failure
}

run_test() {
    local mode=$1 flag=$2 output="$run/$1" code=0
    local bin="$run/build-$flag/src/moonep/combine_ht/tilexr_combine_ht_test"
    python3 -u "$run/profile_worker.py" "$mode" "$source_dir" "$bin" "$output" "$idle_timeout" \
        >"$run/$mode.log" 2>&1 || code=$?
    printf 'mode=%s rc=%s\n' "$mode" "$code"
    if (( code )); then
        tail -n 25 "$run/$mode.log" |
            grep -E '(^waiting |^completed |Error: |^intra-send profiling)' |
            tail -n 8 || true
    fi
    return "$code"
}

status=0
if build OFF >"$run/build-OFF.log" 2>&1; then
    echo build_off=pass
    run_test tokens OFF || status=1
else
    echo build_off=fail
    status=1
fi
if build ON >"$run/build-ON.log" 2>&1; then
    echo build_on=pass
    run_test reduce ON || status=1
    run_test send ON || status=1
else
    echo build_on=fail
    status=1
fi
exit "$status"
