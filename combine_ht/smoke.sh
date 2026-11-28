#!/usr/bin/env bash
# Build the archived commit and exercise each standalone kernel on idle cards.
set -Eeo pipefail
export LC_ALL=C
umask 077

revision=$1
digest=$2
idle_timeout=$3
scale_after_smoke=$4
build_only=$5
root="$HOME/.cache/tilexr-ht-be110cc8"
archive="$root/tilexr-$revision.tar.gz"
repo="$root/src-$revision"
build="$root/build-$revision"
step=unpack
log="$root/unpack.log"
for mode in reduce send; do
    prior_samples=0
    for dev in {0..7}; do
        if [[ -f "$root/$mode-$dev.log" ]] && grep -q 'correctness=pass' "$root/$mode-$dev.log"; then
            prior_samples=$((prior_samples + 1))
        fi
    done
    printf 'diagnostic=retained_%s_pass_ranks_%s\n' "$mode" "$prior_samples"
done
if pgrep -f '[t]ilexr-ht-be110cc8/build-.*[/]tilexr_combine_ht_test --mode' >/dev/null; then
    echo diagnostic=prior_smoke_process_still_running
    exit 1
fi


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
if [[ "$digest" != reuse ]]; then
    printf '%s  %s\n' "$digest" "$archive" | sha256sum -c >"$log" 2>&1
    mkdir -p "$repo"
    tar -xzf "$archive" -C "$repo" >>"$log" 2>&1
fi
cd "$repo"
printf 'revision=%s\n' "$revision"

step=environment
log="$root/cann.log"
source /usr/local/Ascend/cann/set_env.sh >"$log" 2>&1
source scripts/common_env.sh >>"$log" 2>&1
printf 'cann=%s\n' "$(readlink -f "$ASCEND_HOME_PATH")"

if [[ "$digest" != reuse ]]; then
    step=build_configure
    log="$root/build.log"
    cmake -S . -B "$build" -DCMAKE_BUILD_TYPE=Release -DTILEXR_BUILD_COMBINE_HT=ON \
        -DTILEXR_COMBINE_HT_SIM_SEND=OFF -DTILEXR_BUILD_UNITEST=OFF \
        -DTILEXR_ENABLE_PROFILING=OFF -DBUILD_TESTING=ON >"$log" 2>&1
    step=build_compile
    timeout 360 cmake --build "$build" --target tilexr_combine_ht_test -j8 >>"$log" 2>&1
fi
bin="$build/src/moonep/combine_ht/tilexr_combine_ht_test"
step=existing_binary
[[ -x "$bin" ]]
printf 'build=pass\n'
log="$root/host-contract.log"
if timeout 30 "$bin" --mode host >"$log" 2>&1; then
    echo diagnostic=host_contract_pass
else
    printf 'diagnostic=host_contract_rc_%s\n' "$?"
    missing=$(sed -nE 's/.*error while loading shared libraries: ([A-Za-z0-9_.-]+):.*/\1/p' "$log" | head -1)
    if [[ -n "$missing" ]]; then
        printf 'diagnostic=missing_library_%s\n' "$missing"
        exit 1
    fi
    diagnostic=$(grep -E '^(layout|overlapping roles accepted|unused reducer configuration|empty reducer configuration accepted|backward [a-z/ ]+|address negative accepted: [a-z ]+|forward source coverage)(: [0-9]+)?$' "$log" | head -1 || true)
    printf 'diagnostic=host_contract_detail_%s\n' "$diagnostic"
fi
awk '/^MemTotal:/ {print "diagnostic=host_memory_kib_" $2}' /proc/meminfo
if command -v msprof >/dev/null; then echo diagnostic=msprof_present; fi
if [[ "$build_only" == 1 ]]; then exit 0; fi

wait_idle() {
    local deadline=$((SECONDS + idle_timeout)) next_status=$SECONDS dev state
    local -a probes states
    while (( SECONDS < deadline )); do
        for dev in {0..7}; do
            timeout 10 npu-smi info -t proc-mem -i "$dev" >"$root/npu-$dev.log" 2>&1 &
            probes[dev]=$!
        done
        for dev in {0..7}; do
            state=idle
            if ! wait "${probes[dev]}"; then
                state=probe_failed
            elif grep -qiE 'process id[[:space:]]*:' "$root/npu-$dev.log"; then
                state=busy
            elif ! grep -qiE "NPU ID[[:space:]]*:[[:space:]]*$dev([[:space:]]|$)" "$root/npu-$dev.log"; then
                state=unrecognized
            fi
            states[dev]=$state
        done
        if (( SECONDS >= next_status )); then
            printf 'diagnostic=npu_occupancy_mode_%s_devices_0_to_7_%s\n' "$mode" "${states[*]}"
            next_status=$((SECONDS + 90))
        fi
        if [[ "${states[*]}" == 'idle idle idle idle idle idle idle idle' ]]; then return 0; fi
        sleep 2
    done
    return 1
}

rootinfo_ready=1
log="$root/rootinfo-prepare.log"
if python3 - "$root/udma-rootinfo.json" >"$log" 2>&1 <<'PYROOT'
from collections import Counter
import ipaddress
import json
from pathlib import Path
import re
import sys

def filter_addresses(node):
    if isinstance(node, dict):
        return {key: filter_addresses(value) for key, value in node.items()}
    if isinstance(node, list):
        retained = []
        for item in node:
            if isinstance(item, dict) and "addr" in item and not re.fullmatch(r"[a-fA-F0-9]{32}", item["addr"]):
                ipaddress.IPv4Address(item["addr"])
                dropped.append(item)
            else:
                retained.append(filter_addresses(item))
        return retained
    return node

def devices(node):
    if isinstance(node, dict):
        if "device_id" in node:
            yield node
        else:
            for value in node.values():
                yield from devices(value)
    elif isinstance(node, list):
        for item in node:
            yield from devices(item)

def ports(node):
    if isinstance(node, dict):
        if "addr" in node:
            yield len(node["ports"])
        else:
            for value in node.values():
                yield from ports(value)
    elif isinstance(node, list):
        for item in node:
            yield from ports(item)

source = Path("/etc/hccl_rootinfo.json")
dropped = []
result = filter_addresses(json.loads(source.read_text()))
counts = [Counter(ports(device)) for device in devices(result)]
assert len(counts) == 8
assert all(row[1] >= 7 and row[2] == 1 and row[6] == 1 for row in counts)
target = Path(sys.argv[1])
target.write_text(json.dumps(result))
print(f"diagnostic=udma_rootinfo_local_copy_ipv4_removed_{len(dropped)}")
PYROOT
then
    cat "$log"
    export TILEXR_UDMA_ROOTINFO_PATH="$root/udma-rootinfo.json"
else
    echo diagnostic=udma_rootinfo_local_copy_validation_failed
    rootinfo_ready=0
fi

export TILEXR_ENABLE_UDMA=1
unset ASCEND_RT_VISIBLE_DEVICES
failed=0
for mode in reduce send; do
    step="idle-$mode"
    log="$root/npu-0.log"
    if [[ "$mode" == send ]]; then
        if (( !rootinfo_ready )); then
            echo 'mode=send blocked=rootinfo_local_copy_validation'
            failed=1
            continue
        fi
        port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
        export TILEXR_COMM_ID="127.0.0.1:$port"
    fi
    wait_idle
    printf 'devices_idle=8 mode=%s\n' "$mode"
    step="$mode"
    pids=()
    for dev in {0..7}; do
        timeout --kill-after=5 150 "$bin" --mode "$mode" --world 64 --rank "$dev" --device "$dev" \
            --tokens 33 --hidden 256 --topk 6 --chunk 32 --splits 1 --reducers 8 --reduce-begin 0 \
            --intra-cores 1 --iterations 1 --local-comm 1 --domain 0 >"$root/$mode-$dev.log" 2>&1 &
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
            error=$(grep -E '^(complete|communicator|registration|layout|launch|prepare|aclInit|device|send status|empty reducer progress)(: -?[0-9]+)?$' "$log" | tail -1 || true)
            printf 'mode=%s rank=%s rc=%s error=%s\n' "$mode" "$dev" "$rc" "$error"
        fi
    done
    printf 'mode=%s correctness_pass_ranks=%s/8\n' "$mode" "$passed"
    if (( passed != 8 )); then
        failed=1
    elif [[ "$scale_after_smoke" == 1 ]]; then
        campaign="scale-$mode-${revision:0:12}"
        output="$root/$campaign"
        log="$root/$campaign.log"
        script=bench_send.py
        cores=1
        if [[ "$mode" == reduce ]]; then
            script=bench_gateway_reduce.py
            cores=8
        fi
        msprof=$(command -v msprof)
        if python3 -u "tests/moonep_combine_ht/$script" --binary "$bin" --output "$output" \
            --dispatch-ep 64 --combine-ep 8 --server-count 1 --chunks 256 --cores "$cores" \
            --msprof "$msprof" --domain 0 --idle-timeout "$idle_timeout" >"$log" 2>&1; then
            printf 'mode=%s scale=pass campaign=%s\n' "$mode" "$campaign"
        else
            printf 'mode=%s scale=fail campaign=%s\n' "$mode" "$campaign"
            if grep -q 'expected .*tilexr_moonep_combine_ht_kernel tasks' "$log"; then
                echo diagnostic=msprof_sample_count_mismatch
            fi
            if grep -q 'devices still busy' "$log"; then echo diagnostic=scale_idle_wait_expired; fi
            if grep -q 'failed ranks' "$log"; then
                grep -oE 'stage (send|reduce), chunk [0-9]+, cores [0-9]+, failed ranks \[[0-9, ]+\]' "$log" | tail -1
                find "$output" -name stdout.log -print0 |
                    xargs -0 -r grep -hE '^(aclInit|device|stream|malloc|communicator|registration|prepare|launch|complete): -?[0-9]+$|^(send status|empty reducer progress)$' |
                    sort -u | head -16 || true
            fi
            failed=1
        fi
    fi
done
exit "$failed"
