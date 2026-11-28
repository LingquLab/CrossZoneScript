#!/usr/bin/env bash
# Build the archived commit and exercise each standalone kernel on idle cards.
set -Eeo pipefail
export LC_ALL=C
umask 077

revision=$1
digest=$2
root="$HOME/.cache/tilexr-ht-be110cc8"
archive="$root/tilexr-$revision.tar.gz"
repo="$root/src-$revision"
build="$root/build-$revision"
step=unpack
log="$root/unpack.log"

report() {
    local rc=$?
    printf 'step=%s rc=%s\n' "$step" "$rc"
    if [[ -f "$log" ]]; then
        if grep -qi 'No space left on device' "$log"; then echo diagnostic=disk_full; fi
        if grep -qiE 'No such file or directory|command not found' "$log"; then echo diagnostic=missing_file_or_command; fi
        if grep -qi 'cannot find -l' "$log"; then echo diagnostic=library_not_found; fi
        if grep -qiE 'fatal error:|error:' "$log"; then echo diagnostic=error_in_saved_log; fi
    fi
    exit "$rc"
}
trap report ERR
printf '%s  %s\n' "$digest" "$archive" | sha256sum -c >"$log" 2>&1
mkdir -p "$repo"
tar -xzf "$archive" -C "$repo" >>"$log" 2>&1
cd "$repo"
printf 'revision=%s\n' "$revision"

step=environment
log="$root/cann.log"
source /usr/local/Ascend/cann/set_env.sh >"$log" 2>&1
source scripts/common_env.sh >>"$log" 2>&1
printf 'cann=%s\n' "$(readlink -f "$ASCEND_HOME_PATH")"

step=build_configure
log="$root/build.log"
cmake -S . -B "$build" -DCMAKE_BUILD_TYPE=Release -DTILEXR_BUILD_COMBINE_HT=ON \
    -DTILEXR_COMBINE_HT_SIM_SEND=OFF -DTILEXR_BUILD_UNITEST=OFF \
    -DTILEXR_ENABLE_PROFILING=OFF -DBUILD_TESTING=ON >"$log" 2>&1
step=build_compile
timeout 360 cmake --build "$build" --target tilexr_combine_ht_test -j8 >>"$log" 2>&1
step=host_contract
ctest --test-dir "$build" -R '^tilexr_combine_ht_contract$' --output-on-failure >>"$log" 2>&1
printf 'build=pass host_contract=pass\n'

wait_idle() {
    local deadline=$((SECONDS + 450)) dev idle
    local -a probes
    while (( SECONDS < deadline )); do
        for dev in {0..7}; do
            timeout 5 npu-smi info -t proc-mem -i "$dev" >"$root/npu-$dev.log" 2>&1 &
            probes[dev]=$!
        done
        idle=1
        for dev in {0..7}; do
            if ! wait "${probes[dev]}" || ! grep -Eq "NPU ID[[:space:]]*:[[:space:]]*$dev([[:space:]]|$)" "$root/npu-$dev.log" ||
                grep -q 'Process id[[:space:]]*:' "$root/npu-$dev.log"; then
                idle=0
            fi
        done
        if (( idle )); then return 0; fi
        sleep 2
    done
    return 1
}

export TILEXR_ENABLE_UDMA=1
unset ASCEND_RT_VISIBLE_DEVICES
bin="$build/src/moonep/combine_ht/tilexr_combine_ht_test"
failed=0
for mode in reduce send; do
    step="idle-$mode"
    log="$root/npu-0.log"
    wait_idle
    printf 'devices_idle=8 mode=%s\n' "$mode"
    step="$mode"
    if [[ "$mode" == send ]]; then
        port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
        export TILEXR_COMM_ID="127.0.0.1:$port"
    fi
    pids=()
    for dev in {0..7}; do
        timeout --kill-after=5 150 "$bin" --mode "$mode" --world 64 --rank "$dev" --device "$dev" \
            --tokens 33 --hidden 256 --topk 6 --chunk 32 --splits 1 --reducers 8 --reduce-begin 0 \
            --intra-cores 1 --iterations 1 --local-comm 1 >"$root/$mode-$dev.log" 2>&1 &
        pids[dev]=$!
    done
    passed=0
    for dev in {0..7}; do
        log="$root/$mode-$dev.log"
        rc=0
        wait "${pids[dev]}" || rc=$?
        if (( rc == 0 )) && grep -q 'correctness=pass' "$log"; then
            passed=$((passed + 1))
        else
            error=$(grep -E '^(complete|communicator|registration|layout|launch|prepare|aclInit|set device|send status)(: [0-9]+)?$' "$log" | tail -1 || true)
            printf 'mode=%s rank=%s rc=%s error=%s\n' "$mode" "$dev" "$rc" "$error"
        fi
    done
    printf 'mode=%s correctness_pass_ranks=%s/8\n' "$mode" "$passed"
    if (( passed != 8 )); then failed=1; fi
done
exit "$failed"
