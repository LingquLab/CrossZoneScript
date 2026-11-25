"""Decode BS32 SST marker dumps into Perfetto, per-AIV timings, and marker documentation."""
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
import struct

RANKS = 64
BLOCKS = 56
CORE_BYTES = 8192
CYCLES_PER_US = 1650
PROFILE_MAGIC = 0x5458525052463332
DECODER_SHA256 = '6d4cd3b729177646ca1c43d57971e5266a946405b6f820f3d0d914114b7ccfce'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def role_for_core(core):
    return next(role for role, end in zip(ROLE_NAMES, ROLE_BOUNDS[1:]) if core < end)


def decode_owned_sectors(paths, address, length):
    sectors = {}
    for die, path in enumerate(paths):
        with path.open('rb') as stream:
            while block := stream.read(520):
                if len(block) != 520:
                    raise ValueError('truncated addressed-512 profile dump')
                sector, = struct.unpack_from('<Q', block)
                owner = ((sector >> 9) ^ (sector >> 16) ^ (sector >> 23)) & 1
                if owner != die or not address <= sector < address + length:
                    continue
                if sectors.setdefault(sector, block[8:]) != block[8:]:
                    raise ValueError('conflicting profile sector')
    return sectors


def decode_records(sectors, address):
    header = sectors.get(address)
    if header is None:
        raise ValueError('missing lane header')
    magic, words, dropped = struct.unpack_from('<3Q', header)
    if magic != PROFILE_MAGIC or not 12 <= words <= CORE_BYTES // 8 or words % 2 or dropped:
        raise ValueError(f'invalid lane header: count={words}, dropped={dropped}')
    try:
        data = b''.join(sectors[at] for at in range(address, address + words*8, 512))
    except KeyError as error:
        raise ValueError('missing recorded lane sector') from error
    records = [(tick, packed & 0xFFFFFFFF, packed >> 32)
               for tick, packed in struct.iter_unpack('<QQ', data[64:words*8])]
    if any(a[0] > b[0] for a, b in zip(records, records[1:])):
        raise ValueError('non-monotonic lane clock')
    if any(not tick or not event for tick, event, _ in records):
        raise ValueError('zero timestamp or event ID')
    return records


def marker_name(role, event):
    if role in ROLE_NAMES[:2] and event in LOCAL_EVENTS:
        return LOCAL_EVENTS[event]
    if role == 'Gather' and 256 <= event < 256 + 2*16384:
        return 'gather_doorbell' if event % 2 == 0 else 'gather_cqe_consumed'
    for begin, end, name, _, _, _ in MARKERS[role]:
        if event == begin:
            return f'{name}_begin'
        if event == end:
            return f'{name}_end'
    return f'marker_{event}'


def decode_fields(detail, fields):
    if not fields:
        return {}
    if fields == 'peer,lane,batch_rows' or fields == 'peer,lane,piece_wqes' or fields == 'peer,lane,chunk':
        _, _, suffix = fields.split(',')
        return {'peer': detail >> 16, 'lane': (detail >> 8) & 255, suffix: detail & 255}
    if fields == 'token,server':
        return {'token': detail >> 8, 'server': detail & 255}
    return {fields.removesuffix('_end'): detail}


def collect_intervals(records, role):
    pairs = MARKERS[role]
    rows = []
    for begin, end, name, _, _, fields in pairs:
        stack = []
        for tick, event, detail in records:
            if event == begin:
                stack.append((tick, detail))
            elif event == end:
                if not stack:
                    raise ValueError(f'unpaired marker {event} for {name}')
                start, data = stack.pop()
                if fields and not fields.endswith('_end') and data != detail:
                    raise ValueError(f'mismatched detail for {name}')
                rows.append((start, tick, begin, end, name, decode_fields(detail if fields.endswith('_end') else data, fields)))
        if stack:
            raise ValueError(f'incomplete marker pair for {name}')
    return rows


def to_us(cycles):
    return round(cycles / CYCLES_PER_US, 6)


def summarize_stage(samples):
    values = sorted(value for value, _, _ in samples)
    slowest = max(samples)
    return {
        'aivs': len(values), 'median_us': to_us(median(values)),
        'p95_us': to_us(values[math.ceil(len(values)*0.95)-1]),
        'max_us': to_us(slowest[0]), 'max_rank': slowest[1], 'max_aiv': slowest[2],
    }


def read_tracks(case):
    workload = json.loads((case/'workload.yml').read_text())['workload_config']
    report = json.loads((case/'actual/export-report.json').read_text())
    if report['decoder_sha256'] != DECODER_SHA256:
        raise ValueError('unrecognized Die decoder')
    tracks, invalid = {}, []
    for rank in range(RANKS):
        chip = workload[f'chip{rank}']
        region = chip['output_param']['profile']
        address = region['addr'] - chip['sys_va_base_addr']
        assert address % 512 == 0 and region['size'] == CORE_BYTES * BLOCKS
        paths = [case/'bin'/f'david_{rank}_die{die}_profile.bin' for die in (1, 2)]
        sectors = decode_owned_sectors(paths, address, region['size'])
        for core in range(BLOCKS):
            role = role_for_core(core)
            try:
                records = decode_records(sectors, address + core*CORE_BYTES)
                begin, end = MARKERS[role][0][:2]
                if records[0][1] != begin or records[-1][1] != end:
                    raise ValueError('missing role bounds')
                tracks[rank, core] = records, collect_intervals(records, role)
            except ValueError as error:
                invalid.append([rank, core, str(error)])
    return tracks, invalid


def render(case, output, marker_locations):
    run = json.loads((case/'unified-result.json').read_text())
    if run['status'] != 'sst_and_checker_passed':
        raise ValueError('workload output has not passed the checker')
    tracks, invalid = read_tracks(case)
    if not tracks:
        raise ValueError('no complete AIV lanes')
    origin = min(records[0][0] for records, _ in tracks.values())
    events, totals = [], defaultdict(list)
    roles = defaultdict(lambda: {'first': None, 'last': None})
    counts = Counter()
    unknown = Counter()
    per_aiv = []
    for (rank, core), (records, intervals) in sorted(tracks.items()):
        role = role_for_core(core)
        bounds = roles[role]
        first, last = records[0][0], records[-1][0]
        bounds['first'] = first if bounds['first'] is None else min(bounds['first'], first)
        bounds['last'] = last if bounds['last'] is None else max(bounds['last'], last)
        if core == 0:
            events.append(dict(ph='M', name='process_name', pid=rank, tid=0, args={'name': f'Rank {rank:02}'}))
        events.append(dict(ph='M', name='thread_name', pid=rank, tid=core, args={'name': f'AIV {core:02} · {role}'}))
        track_totals = Counter()
        for start, stop, begin, end, name, fields in sorted(intervals, key=lambda row: (row[0], -row[1])):
            track_totals[name] += stop-start
            counts[name] += 1
            events.append(dict(ph='X', name=f'{name} [{begin}–{end}]', cat=role, pid=rank, tid=core,
                               ts=to_us(start-origin), dur=to_us(stop-start),
                               args=dict(marker_begin_id=begin, marker_end_id=end, **fields)))
        for tick, event, detail in records:
            args = {'marker_id': event, 'detail_raw': detail}
            if role == 'Gather' and 256 <= event < 256 + 2*16384:
                args['sq_bb'] = (event - 256) // 2
            label = marker_name(role, event)
            if label.startswith('marker_'):
                unknown[role, event] += 1
            events.append(dict(ph='i', s='t', name=f'{label} [{event}]', cat='raw_marker',
                               pid=rank, tid=core, ts=to_us(tick-origin), args=args))
        for name, elapsed in track_totals.items():
            totals[name].append((elapsed, rank, core))
            per_aiv.append([rank, core, role, name, to_us(elapsed)])
    summary = {
        'sst_sim_us': run['sim_us'],
        'kernel_sha256': run['kernel_sha256'],
        'checked_output_values': run['checked']['bytes_checked']['output']//4,
        'guards': run['guards']['count'],
        'streams': len(tracks), 'expected_streams': RANKS*BLOCKS, 'invalid': invalid,
        'unknown_markers': [[role, event, count] for (role, event), count in sorted(unknown.items())],
        'slices': sum(counts.values()), 'raw_markers': sum(len(records) for records, _ in tracks.values()),
        'clock_cycles_per_us': CYCLES_PER_US, 'origin_cycle': origin,
        'role_envelopes': {role: {'first_us': to_us(v['first']-origin), 'last_us': to_us(v['last']-origin)}
                           for role,v in roles.items()},
        'stages': {name: dict(summarize_stage(values), slices=counts[name]) for name, values in totals.items()},
    }
    output.mkdir(exist_ok=True)
    (output/'perfetto_trace.with-markers.json').write_text(
        json.dumps({'traceEvents': events, 'displayTimeUnit': 'us'}, ensure_ascii=False, separators=(',', ':')),
        encoding='utf-8')
    (output/'profile-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    with (output/'per-aiv-us.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['rank', 'aiv', 'role', 'interval', 'total_us'])
        writer.writerows(per_aiv)
    write_report(output/'stage-report.md', summary, marker_locations)
    return summary


def write_report(path, summary, marker_locations):
    lines = [
        '# BS32 HT Combine SST 打点报告', '',
        'EP64、BS32、H128 FP32、56 AIV。五阶段在同一 SST 运行内执行。', '',
        f"打点版模拟耗时 **{summary['sst_sim_us']:.3f} µs**，通过 {summary['checked_output_values']:,} 个输出和 {summary['guards']} 个 guard 的校验。"
        f"导出 {summary['streams']:,}／{summary['expected_streams']:,} 条 AIV 轨道、{summary['slices']:,} 个区间和 {summary['raw_markers']:,} 个原始打点。", '',
        '同夹具无打点版的模拟耗时为 258.152 µs。打点版执行计时、缓冲区写入和导出，Gateway 的逐 token 详细打点还会等 MTE3 写回完成。', '',
        'Perfetto 按 rank 和 AIV 显示轨道。区间的参数保留起止打点号。原始打点显示为瞬时事件。'
        '同一轨道上可按时间比较等待、发布和写回。', '',
        '所有时间采用 1,650 cycle/µs。横轴从本次最早的设备打点开始。'
        '中位数、P95 和最大值按每个 AIV 在该阶段的累计耗时计算。父区间包含其子区间，两者相加会重复计时。', '',
        '| 角色 | AIV 编号 | 最早开始 µs | 最晚结束 µs |', '| --- | --- | ---: | ---: |',
    ]
    for index, role in enumerate(ROLE_NAMES):
        envelope = summary['role_envelopes'].get(role)
        if envelope:
            lines.append(f"| {role} | {ROLE_BOUNDS[index]}–{ROLE_BOUNDS[index+1]-1} | {envelope['first_us']:.3f} | {envelope['last_us']:.3f} |")
    for role, pairs in MARKERS.items():
        lines.extend(('', f'## {role}', '', '| 起止打点号 | Perfetto 区间 | 记录的工作 | 源码位置 | AIV 数 | 中位数 µs | P95 µs | 最大 µs |',
                      '| --- | --- | --- | --- | ---: | ---: | ---: | ---: |'))
        for begin, end, name, meaning, function, _ in pairs:
            stage = summary['stages'].get(name)
            source = ROLE_SOURCE[role]
            locations = marker_locations.get(source, {})
            loc = locations.get(str(begin), '?')
            end_loc = locations.get(str(end))
            source_label = f'{source}:{loc}' if end_loc is None else f'{source}:{loc}, {end_loc}'
            if stage:
                metrics = f"{stage['aivs']} | {stage['median_us']:.3f} | {stage['p95_us']:.3f} | {stage['max_us']:.3f}"
            else:
                metrics = '0 | — | — | —'
            lines.append(f'| {begin} → {end} | `{name}` | {meaning} | `{source_label}` `{function}` | {metrics} |')
    lines.extend(('', '## 原始瞬时打点', '', '| 打点号 | 含义 | 函数 |', '| --- | --- | --- |'))
    for event, (meaning, function) in POINTS.items():
        lines.append(f'| {event} | {meaning} | `{function}` |')
    lines.extend((
        '| 256 + 2 × BB | Gather 对应 SQ 批次敲 doorbell，BB 为批次最后一个 SQ 位置 | `LocalPutBatch::Flush` |',
        '| 257 + 2 × BB | Gather 在 CQ 轮询中消费该 BB 的完成记录 | `LocalPutBatch::Completed` |',
        '',
        'Gather 的 doorbell 打点对应批次末尾 BB，CQ 消费打点对应单个已完成 BB。',
        '结束点记录 AIV 观察到完成的时刻。',
    ))
    if summary['invalid']:
        lines.extend(('', f"{len(summary['invalid'])} 条轨道解析失败，详见 `profile-summary.json` 的 `invalid`。"))
    if summary['unknown_markers']:
        lines.extend(('', '尚未命名的打点号和出现次数见 `profile-summary.json` 的 `unknown_markers`。'))
    path.write_text('\n'.join(lines)+'\n', encoding='utf-8')
