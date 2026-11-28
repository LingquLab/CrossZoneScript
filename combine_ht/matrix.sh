#!/usr/bin/env bash
# Run a single module and keep profiling files on the test server for green retrieval.
set -Eeo pipefail
export LC_ALL=C
umask 077
revision=$1
campaign=$2
script=$3
mode=$4
shift 4
root="$HOME/.cache/tilexr-ht-be110cc8"
src="$root/src-$revision"
bin="$root/build-$revision/src/moonep/combine_ht/tilexr_combine_ht_test"
output="$root/$campaign"
log="$root/$campaign.log"

report() {
    local rc=$?
    printf 'diagnostic=remote_status_%s\n' "$rc"
    if [[ -f "$log" ]]; then
        if grep -q 'devices still busy' "$log"; then echo diagnostic=idle_wait_expired; fi
        if grep -q 'failed ranks' "$log"; then
            grep -oE 'stage (send|reduce), chunk [0-9]+, cores [0-9]+, failed ranks \[[0-9, ]+\]' "$log" | tail -1
            find "$output" -name stdout.log -print0 |
                xargs -0 -r grep -hE '^(aclInit|device|stream|malloc|communicator|registration|prepare|launch|complete): -?[0-9]+$|^(send status|empty reducer progress)$' |
                sort -u | head -16 || true
        fi
        if grep -q 'expected .*tilexr_moonep_combine_ht_kernel tasks' "$log"; then echo diagnostic=msprof_sample_count_mismatch; fi
    fi
    exit "$rc"
}
trap report ERR
cd "$src"
source /usr/local/Ascend/cann/set_env.sh >"$log" 2>&1
source scripts/common_env.sh >>"$log" 2>&1
if [[ "$mode" == send ]]; then
    export TILEXR_UDMA_ROOTINFO_PATH="$root/udma-rootinfo.json"
    test -r "$TILEXR_UDMA_ROOTINFO_PATH"
fi
test -x "$bin"
unset ASCEND_RT_VISIBLE_DEVICES
msprof=$(command -v msprof)
python3 -u "tests/moonep_combine_ht/$script" --binary "$bin" --output "$output" \
    --dispatch-ep 64 --combine-ep 8 --msprof "$msprof" "$@" 2>&1 | tee -a "$log"
if [[ "$campaign" == matrix-* ]]; then
    python3 "$root/collect-$campaign.py" "$output"
fi
