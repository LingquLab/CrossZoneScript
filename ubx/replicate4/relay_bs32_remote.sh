#!/usr/bin/env bash
set -eo pipefail

root=$1
comparison=${3:-default}
operation=${4:-build}
phase=verify
trap 'status=$?; printf "%s %s\n" "$phase" "$status" > "$root/status.txt"' EXIT
printf '%s\n' "$phase" > "$root/status.txt"
if [[ $operation == resume ]]; then
    test ! -e "$root/run"
    sha256sum --check --status "$root/artifacts.sha256"
else
    printf '%s  %s\n' "$2" "$root/sources.tar" | sha256sum --check --status
    tar -xf "$root/sources.tar" -C "$root"
    mkdir "$root/source" "$root/runtime"
    tar -xf "$root/source.tar.gz" -C "$root/source"
    tar -xf "$root/runtime.tar.gz" -C "$root/runtime"
    for dep in spdlog picojson; do
        tar -xf "$root/$dep.tar.gz" -C "$root/runtime/3rdparty/$dep"
    done
fi

phase=environment
printf '%s\n' "$phase" > "$root/status.txt"
for toolkit in /usr/local/Ascend/cann-9.2.T101/set_env.sh /usr/local/Ascend/ascend-toolkit/set_env.sh; do
    if [[ -f $toolkit ]]; then
        source "$toolkit"
        break
    fi
done
test -n "${ASCEND_HOME_PATH:-}"
printf '%s\n' "$ASCEND_HOME_PATH" > "$root/toolkit.txt"

release="$root/source/dispatch/dispatch-release"
if [[ $operation == build ]]; then
    phase=runtime-build
    printf '%s\n' "$phase" > "$root/status.txt"
    cmake -S "$root/runtime" -B "$root/runtime-build" -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
        -DTILEXR_BUILD_TESTS=OFF -DTILEXR_BUILD_UNITEST=OFF -DTILEXR_BUILD_RDMA=ON \
        -DCMAKE_INSTALL_PREFIX="$root/install"
    cmake --build "$root/runtime-build" -j8
    cmake --install "$root/runtime-build"

    phase=benchmark-build
    printf '%s\n' "$phase" > "$root/status.txt"
    cmake -S "$release" -B "$root/build" -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
        -DUBX_BUILD_HARDWARE_TESTS=ON -DTILEXR_INSTALL="$root/install"
    cmake --build "$root/build" --target ubx_relay_bench_worker -j8
    sha256sum "$root/build/ubx_dispatch_ep32.o" "$root/build/ubx_dispatch_round_barrier.o" \
        "$root/build/ubx_relay_bench_worker" > "$root/artifacts.sha256"
fi

phase=occupancy
printf '%s\n' "$phase" > "$root/status.txt"
python3 - "$root" "$operation" <<'PY'
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
import sys
import time

def probe(device):
    try:
        result = subprocess.run(['npu-smi', 'info', '-t', 'proc-mem', '-i', str(device)],
                                capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and re.search(rf'NPU ID\s*:\s*{device}\b', result.stdout):
            if re.search(r'Process id\s*:\s*\d+', result.stdout, re.I):
                return 'busy'
            if 'No process in device' in result.stdout:
                return 'idle'
    except (OSError, subprocess.TimeoutExpired):
        pass
    return 'unknown'

deadline = time.monotonic() + (120 if sys.argv[2] == 'resume' else 0)
while True:
    with ThreadPoolExecutor(max_workers=8) as pool:
        devices = list(pool.map(probe, range(16)))
    occupancy = {state: [i for i, value in enumerate(devices) if value == state]
                 for state in ('idle', 'busy', 'unknown')}
    (Path(sys.argv[1]) / 'occupancy.json').write_text(json.dumps(occupancy))
    if occupancy['idle'] == list(range(16)) or time.monotonic() >= deadline:
        break
    time.sleep(5)
assert occupancy['idle'] == list(range(16)), occupancy
PY

phase=benchmark
printf '%s\n' "$phase" > "$root/status.txt"
export LD_LIBRARY_PATH="$root/install/lib64:$root/install/lib:${LD_LIBRARY_PATH:-}"
mode=()
if [[ $comparison == pairings ]]; then
    mode+=(--pairings)
fi
python3 "$release/tests/hardware/run_relay_bench.py" --build "$root/build" \
    --output "$root/run" --measured 50 --port 30500 "${mode[@]}"

phase=verify-results
printf '%s\n' "$phase" > "$root/status.txt"
python3 - "$root" "$comparison" <<'PY'
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]); result = root / 'run'
summary = json.loads((result / 'summary.json').read_text())
groups = {'all_groups': 4, 'paired_groups': 2, 'same_group': 1}
cases = [(mode, 1) for mode in groups] if sys.argv[2] == 'default' else [
    ('paired_groups', pair_mask) for pair_mask in (1, 2, 3)]
assert (summary['bs'], summary['ranks'], summary['warmup'], summary['measured']) == (32,16,10,50)
assert [(c['workers'], c['mode'], c.get('pair_mask', 1)) for c in summary['cases']] == [
    (workers, mode, pair_mask) for workers in (16,32) for mode, pair_mask in cases]
if sys.argv[2] == 'pairings':
    partners = {1: [[0,1],[2,3]], 2: [[0,2],[1,3]], 3: [[0,3],[1,2]]}
    for c in summary['cases']:
        assert c['bytes_per_rank'] == 524288 and c['cross_group_writes_per_rank'] == 64
        assert [pair['groups'] for pair in c['pairs']] == partners[c['pair_mask']]
rank_rows = [list(map(json.loads, (result / f'rank{rank}.jsonl').read_text().splitlines())) for rank in range(16)]
assert all(len(rows) == 300 for rows in rank_rows)
last = [row for rows in rank_rows for row in rows if row['round'] == 49]
assert len(last) == 96
peaks = []
for case in summary['cases']:
    peak, = (row for row in rank_rows[case['slowest_rank']]
             if (row['workers'], row['groups'], row.get('pair_mask', 1), row['round']) ==
             (case['workers'], groups[case['mode']], case.get('pair_mask', 1), case['slowest_round']))
    peaks.append(peak)
(result / 'last-round.json').write_text(json.dumps(last, indent=2) + '\n')
(result / 'slowest-rank-rounds.json').write_text(json.dumps(peaks, indent=2) + '\n')
PY
phase=complete
