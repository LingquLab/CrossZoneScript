"""Rerun EP32 dual landing and compare all measured RDMA sender spans."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex

import benchmark
import retest_two_groups

DUAL = "9d0d69bfaee6e957a4913be3fdabf9ace5cf9f4e"
SINGLE = "cdeffb1ded6f98ed9eff46f6c66259ba8e9e1ad6"
HOSTS = ("90.90.93.83", "90.90.93.86")

READ_TIMINGS = r"""
import json
import re
import struct
import sys
from pathlib import Path

revision, bs, first, route_hash, name = sys.argv[1:]
bs, first = int(bs), int(first)
root = Path('/root/ubx-replicate4-rdma-two-groups-bs32-' + revision[:7])
prefix = f'full-bs{bs}-f50'
runs = [root / name] if name != '-' else sorted(root.glob(prefix + '*'))
for output in reversed(runs):
    if not re.fullmatch(prefix + r'(?:-[a-z0-9-]+)?', output.name):
        continue
    receipt_path = output / 'host-result.json'
    if not receipt_path.is_file():
        continue
    receipt = json.loads(receipt_path.read_text())
    if (receipt.get('revision'), receipt.get('warmup'), receipt.get('measured'),
            receipt.get('profile_mode'), receipt.get('passed')) != (revision, 10, 50, 'full', True):
        continue
    sample = output / f'rank{first}' / 'rounds.jsonl'
    row = json.loads(sample.open().readline())
    if row.get('route_sha256') == route_hash:
        break
else:
    raise RuntimeError('No matching saved full-profile run')

metrics = {key: [[] for _ in range(50)] for key in ('submit', 'cq_wait', 'doorbell_to_cq', 'rank_span')}
blocks = 0
event_ids = {34, 35, 38, 39, 108}
for rank in range(first, first + 16):
    directory = output / f'rank{rank}'
    queues = json.loads((directory / 'rdma-queues.json').read_text())
    assert len(queues) == 16 and all(q['error'] == 0 and q['producer'] == q['completed'] for q in queues)
    blocks += sum(q['producer'] for q in queues)
    rows = (directory / 'rounds.jsonl').read_text().splitlines()
    assert len(rows) == 60
    with (directory / 'events.bin').open('rb') as raw:
        for i, text in enumerate(rows[10:]):
            row = json.loads(text)
            assert (row['rank'], row['bs'], row['round'], row['route_sha256'], row['tick_hz']) == (
                rank, bs, i + 10, route_hash, 1000000000)
            cores = row['cores']
            assert [c['core'] for c in cores] == list(range(56))
            metrics['rank_span'][i].append(max(c['profile']['end'] for c in cores) -
                                            min(c['profile']['start'] for c in cores))
            for core in cores[:16]:
                raw.seek(core['event_offset'])
                count = core['profile']['count']
                raw_events = raw.read(count * 16)
                assert len(raw_events) == count * 16
                events = {packed & 0xffff: stamp for packed, stamp in struct.iter_unpack('<QQ', raw_events)
                          if packed & 0xffff in event_ids}
                if not events:
                    continue
                assert set(events) == event_ids
                assert events[34] <= events[108] <= events[35] <= events[38] <= events[39]
                metrics['submit'][i].append(events[35] - events[34])
                metrics['cq_wait'][i].append(events[39] - events[38])
                metrics['doorbell_to_cq'][i].append(events[39] - events[108])
assert blocks % 120 == 0
print(json.dumps(dict(run=output.name, tokens=blocks // 120, metrics=metrics), separators=(',', ':')))
"""


def sender_stats(pool, revision, bs, route_hash, run_name):
    def read(index):
        command = shlex.join(["python3", "-", revision, str(bs), str(index * 16), route_hash, run_name])
        return json.loads(benchmark.ssh(HOSTS[index], command, payload=READ_TIMINGS.encode(),
                                        timeout=180, stage=f"BS{bs} send timing"))

    halves = list(pool.map(read, range(2)))
    assert halves[0]["run"] == halves[1]["run"]
    result = {"run": halves[0]["run"], "tokens_per_host": [host["tokens"] for host in halves]}
    for metric in halves[0]["metrics"]:
        rounds = [a + b for a, b in zip(*(host["metrics"][metric] for host in halves))]
        result[metric] = {
            "all": benchmark.timings([ns / 1000 for samples in rounds for ns in samples]),
            "round_max": benchmark.timings([max(samples) / 1000 for samples in rounds]),
        }
    return result


def compact(item):
    result = {"tokens_per_host": item["tokens_per_host"]}
    for metric in ("submit", "cq_wait", "doorbell_to_cq", "rank_span"):
        stats = item[metric]["round_max"]
        result[metric] = [round(stats[key], 3) for key in ("mean_us", "p95_us", "max_us")]
    return result


def main(args):
    destination = Path(args["destination"])
    assert destination.is_absolute()
    destination.mkdir(parents=True, exist_ok=True)
    results = []
    with ThreadPoolExecutor(2) as pool:
        for bs in args["batch_sizes"]:
            run_name = f"full-bs{bs}-f50-dual-send-timing-01"
            summary = retest_two_groups.main(dict(
                args, revision=DUAL, bs=bs, run_name=run_name, hosts=HOSTS,
                destination=str(destination), verify_trace_identity=False, idle_wait=120,
                output_layout="receiver_global_expert_order_v1",
            ))
            route_hash = summary["route_sha256"]
            dual = sender_stats(pool, DUAL, bs, route_hash, run_name)
            single = sender_stats(pool, SINGLE, bs, route_hash, "-")
            full_report = {"bs": bs, "route_sha256": route_hash, "dual": dual, "single": single}
            path = Path(summary["local_results"]) / "rdma-send-comparison.json"
            path.write_text(json.dumps(full_report, indent=2) + "\n", encoding="utf-8")
            results.append(dict(bs=bs, dual=compact(dual), single=compact(single),
                                trace_count=summary["trace_count"], results=str(path.parent)))
    print(json.dumps({"columns": ["mean_us", "p95_us", "max_us"], "cases": results}, separators=(",", ":")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    main(json.loads(parser.parse_args().args_file.read_text(encoding="utf-8")))
