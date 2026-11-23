"""Export compact rank and stage measurements from verified LR8 SST captures."""
import argparse
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text())
assert args.get("mode") in (None, "no-credit", "credit", "clock")
assert args.get("half", 0) in (0, 1)

source = 'OPTIONS = ' + repr(args) + '\n' + r'''
from collections import defaultdict
import json
from pathlib import Path
import struct

output = {}
for mode, label, schedule in (
    ('no-credit', 'matrix-32p-16m-fix', 0),
    ('credit', 'matrix-32p-16m-fix', 4294967295),
    ('clock', 'matrix-32p-16m-clock-boundaryconfirm', 6311440),
):
    if OPTIONS.get('mode') and mode != OPTIONS['mode']:
        continue
    root, = Path('/opt').glob('tilexr-lr8-0c04d831-' + label + '-*')
    case = root / f'32p-131072i32-{schedule}-measurement'
    run = json.loads((case / 'execution.json').read_text())
    assert run['state'] == 'simulation_outputs_verified' and run['runner_returncode'] == 0
    assert not run['remaining_processes']
    provenance = json.loads((case / 'production_provenance.json').read_text())
    assert provenance['kernel_sha256'] == '4e70ba1adc8950cfdff8e1828c67342f4803bd0a54ddb3e88c2b045a30786afb'
    result = dict(schedule=schedule, simulated_us=run['simulated_us'], kernel_sha256=provenance['kernel_sha256'],
                  runtime_commit=provenance['runtime_commit'], ranks=[], stages={})
    measures = defaultdict(list)
    core_export = OPTIONS.get('mode') is not None
    rank_start = OPTIONS.get('half', 0) * 16 if core_export else 0
    for rank in range(rank_start, rank_start + (16 if core_export else 32)):
        data = (case / 'stage-profiling' / f'rank{rank}.records.bin').read_bytes()
        assert data[:8] == b'TXRPRF1\0'
        offset = 48
        cores = {}
        while offset < len(data):
            stream, count, lost, flags, size = struct.unpack_from('<IIIIQ', data, offset)
            core = stream & 65535
            assert stream >> 16 == rank and core < 33 and core not in cores
            assert not lost and size == 24 + count * 16
            records = [(event, tick) for tick, event, _ in struct.iter_unpack('<QII', data[offset+24:offset+size])]
            assert records[0][0] == 1 and records[-1][0] == 2
            assert all(a[1] <= b[1] for a,b in zip(records, records[1:]))
            cores[core] = records
            offset += size
        assert offset == len(data) and len(cores) == 33
        if core_export:
            result.setdefault('cores', []).append([rank, []])
            rank_rows = result['cores'][-1][1]
        groups = [[], []]
        for core in range(32):
            events = defaultdict(list)
            for event, tick in cores[core]:
                events[event].append(tick)
            lane, route = core % 16, core // 16
            active = []
            for group in range(2):
                distance = group * 8 + lane % 8 + 1
                if lane >= 8 and distance == 16:
                    continue
                peer = (rank + (distance if lane < 8 else -distance)) % 32
                if route == 0 or rank // 8 != peer // 8:
                    active.append(group)
            assert all(len(events[event]) == len(active) for event in range(10,18))
            if core_export:
                rank_rows.append([core, events[1][0], events[2][0]])
                row = rank_rows[-1]
            previous = events[1][0]
            for i, group in enumerate(active):
                ticks = [events[event][i] for event in range(10,18)]
                assert previous <= ticks[0] and ticks == sorted(ticks)
                if core_export:
                    row.append([group, ticks[0], *(b - a for a, b in zip(ticks, ticks[1:]))])
                groups[group].append((ticks[0], ticks[-1]))
                durations = [ticks[1]-ticks[0], ticks[3]-ticks[2], ticks[5]-ticks[4],
                             ticks[7]-ticks[6], ticks[0]-previous, ticks[7]-ticks[0]]
                for name, duration in zip(('post','signal','flush','quiet','before_submit','send_span'),durations):
                    measures[f'g{group}.r{route}.{name}'].append(duration)
                previous = ticks[-1]
        receiver = defaultdict(list)
        for event, tick in cores[32]:
            receiver[event].append(tick)
        assert len(receiver[24]) == len(receiver[25]) == 31
        assert len(receiver[22]) == len(receiver[23]) == 32
        wait = [b-a for a,b in zip(receiver[22], receiver[23])]
        copy = [b-a for a,b in zip(receiver[24], receiver[25])]
        self_copy = receiver[21][0]-receiver[20][0]
        assert min(wait+copy+[self_copy]) >= 0
        if core_export:
            rank_rows.append([32, cores[32][0][1], cores[32][-1][1],
                              [receiver[20][0], receiver[21][0], receiver[22][0],
                               receiver[25][-1], sum(wait), sum(copy)]])
            continue
        measures['recv.scan'].extend(wait)
        measures['recv.handle'].extend(copy)
        measures['recv.self_copy'].append(self_copy)
        result['ranks'].append([rank, min(c[0][1] for c in cores.values()),
            max(c[-1][1] for c in cores.values()), *[value for g in groups for value in
            (min(a for a,b in g),max(b for a,b in g))], receiver[22][0],receiver[25][-1],
            sum(wait),sum(copy),self_copy])
    for name, values in sorted(measures.items()):
        ordered = sorted(values)
        result['stages'][name] = [len(ordered), sum(ordered), ordered[0],
                                  ordered[(len(ordered)-1)//2], ordered[(95*len(ordered)+99)//100-1], ordered[-1]]
    output[mode] = result
encoded = json.dumps(output,separators=(',',':'))
assert len(encoded) < 62000, len(encoded)
print(encoded)
'''
result = subprocess.run(
    ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'IdentitiesOnly=yes',
     '-i', 'C:/Users/l00929943/.ssh/id_ed25519_server', 'root@141.61.94.30', 'python3 -'],
    input=source, text=True, capture_output=True, timeout=75)
if result.returncode:
    print(json.dumps(dict(ssh_exit=result.returncode, error=result.stderr.splitlines()[-2:])))
    raise SystemExit(0)
print(result.stdout, end='')
