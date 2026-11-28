"""Summarize EP32 rank spans and last-round relay stages in the green workspace."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path, PurePosixPath
import shlex

import benchmark

HOSTS = ("90.90.93.83", "90.90.93.86")
REPORT = """
import json
from pathlib import Path
import sys

output = Path(sys.argv[1])
revision = sys.argv[2]
first_rank = int(sys.argv[3])
receipt = json.loads((output / 'host-result.json').read_text())
assert receipt['passed'] and receipt['revision'] == revision
assert (receipt['warmup'], receipt['measured']) == (10, 50)
result = {}
for rank in range(first_rank, first_rank + 16):
    rows = json.loads((output / f'rank{rank}/core-total-records.json').read_text())
    assert len(rows) == 60
    durations = []
    for number, row in enumerate(rows[10:], 10):
        assert (row['round'], row['rank'], row['tick_hz']) == (number, rank, 1000000000)
        assert [core['core'] for core in row['cores']] == list(range(56))
        durations.append(max(core['end'] for core in row['cores']) -
                         min(core['start'] for core in row['cores']))
    result[rank] = durations
print(json.dumps(result))
"""


def rank_spans(pool, revision, run_name):
    root = PurePosixPath("/root/ubx-replicate4-rdma-two-groups-bs32-" + revision[:7])
    receipts = []
    for index, host in enumerate(HOSTS):
        command = shlex.join(["python3", "-", str(root / run_name), revision, str(index * 16)])
        receipts.append(pool.submit(benchmark.ssh, host, command, payload=REPORT.encode(),
                                    timeout=60, stage="rank spans"))
    return {int(rank): spans for receipt in receipts
            for rank, spans in json.loads(receipt.result()).items()}


def stages(directory, overlap):
    local_plan, dedicated_plan, drain_times = [], [], []
    confirmed = 0
    for rank in range(32):
        events = json.loads((directory / f"rank{rank}/dispatch.last.trace.json").read_bytes())["traceEvents"]
        by_core = [[] for _ in range(56)]
        for event in events:
            core = event.get("tid")
            if event.get("ph") == "X" and isinstance(core, int) and 16 <= core < 40:
                by_core[core].append(event)
        for core in range(16, 40):
            spans = sorted(by_core[core], key=lambda event: event["ts"])
            plan = [event for event in spans if event["name"] == "planning"][-1]
            if core >= 24:
                dedicated_plan.append(plan["dur"])
                continue
            local_plan.append(plan["dur"])
            if overlap:
                drain = next(event for event in spans if event["name"] == "local_sender_completion_wait")
                route = next(event for event in spans if event["name"] == "route_peer_flag_wait")
                drain_times.append(drain["dur"])
                confirmed += route["ts"] <= plan["ts"] <= drain["ts"]
    data = dict(local_sender_relay_plan=benchmark.timings(local_plan),
                dedicated_relay_plan=benchmark.timings(dedicated_plan))
    if overlap:
        assert confirmed == len(drain_times) == 32 * 8
        data.update(local_drain=benchmark.timings(drain_times), route_plan_drain_order=confirmed)
    return data


def summarize(pool, revision, destination, bs, run_name, baseline):
    directory = destination / f"replicate4-rdma-two-groups-bs{bs}-{revision[:7]}-83-86"
    all_spans = rank_spans(pool, revision, run_name)
    assert sorted(all_spans) == list(range(32))
    peak, rank, round_id = max((span, rank, index + 10) for rank, values in all_spans.items()
                               for index, span in enumerate(values))
    last, last_rank = max((values[-1], rank) for rank, values in all_spans.items())
    report = dict(
        bs=bs,
        rank_all=benchmark.timings([span / 1000 for values in all_spans.values() for span in values]),
        round_rank_max=benchmark.timings([max(spans) / 1000 for spans in zip(*all_spans.values())]),
        slowest_rank=dict(rank=rank, round=round_id, us=peak / 1000),
        last_rank=dict(rank=last_rank, us=last / 1000),
        last_round_stages=stages(directory, overlap=True),
    )
    baseline_directory = destination / f"replicate4-rdma-two-groups-bs{bs}-{baseline}-83-86"
    if baseline_directory.is_dir():
        report["baseline_last_round_stages"] = stages(baseline_directory, overlap=False)
    (directory / "rank-timings.json").write_text(json.dumps(dict(
        revision=revision, **report,
        ranks={rank: benchmark.timings([span / 1000 for span in values])
               for rank, values in all_spans.items()}), indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    revision, baseline = args["revision"], args["baseline"]
    assert len(revision) == 40 and all(c in "0123456789abcdef" for c in revision)
    assert len(baseline) == 7 and all(c in "0123456789abcdef" for c in baseline)
    destination = Path(args["destination"])
    assert destination.is_absolute()
    with ThreadPoolExecutor(2) as pool:
        results = [summarize(pool, revision, destination, int(bs), run_name, baseline)
                   for bs, run_name in args["runs"].items()]
    print(json.dumps(results, separators=(",", ":")))
