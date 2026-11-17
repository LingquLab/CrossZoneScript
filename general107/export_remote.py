"""CPU-only reanalysis and evidence packaging of the completed ABI3 experiment."""
from pathlib import Path
import csv
import hashlib
import json
import math
import statistics
import sys
import zipfile

ROOT = Path('/root/tilelang-107-general-64ed5828-20261117-run02')
OUT = ROOT / 'validated-export01'
sys.path.insert(0, str(ROOT))
from operations import integrity, require, save, sha

REVISION = 'dce8baa3572fd7f5b6ee31c523985f52ff9e0fe3'
BUILD_SHA = '4e34d3a988b2d6219ea30e934a462c9207b894cf44b6e33b4d7295792b8c4c5c'
PARENT_SHA = '867dd8da81cd302da50c3286eeb686365de303fae4722299b5344f059b336f12'
WORKER_SHA = 'c4b26b9be551c6dc82402de097d37b000cd60d09582e410d500e2f4f14fbe166'
MAIN_METRICS = ['module.whole', 'module.public_prepare', 'module.count_publish',
                'module.pack_send', 'module.layout_prefix', 'module.receive_output.receiver',
                'module.receive_output.non_receiver', *['F' + str(i) + '.all' for i in range(7)],
                'F7.receiver', 'F7.non_receiver', 'F8.receiver', 'F9.receiver', 'F8+F9.receiver']
FOUR_US = ('dispatch_acl_us', 'msprof_dispatch_us', 'pre_dispatch_event_us', 'segment_event_us')


def distribution(values):
    require(values and all(math.isfinite(v) for v in values), 'Invalid distribution')
    return dict(n=len(values), mean=statistics.mean(values), median=statistics.median(values),
                min=min(values), max=max(values))


def aggregate(rows, metric):
    present = [(round_no, row['statistics'][metric]) for round_no, row in rows
               if row['statistics'][metric]['participants']]
    if not present:
        return dict(status='NOT_EXECUTED', participants=0, mean_ticks=None, max_ticks=None, slowest=[])
    require(len(present) == len(rows), 'Participation changes across rounds')
    participants = {stat['participants'] for _, stat in present}
    require(len(participants) == 1, 'Different core denominators across rounds')
    maximum = max(stat['max_ticks'] for _, stat in present)
    return dict(status='EXECUTED', participants=participants.pop(),
                mean_ticks=statistics.mean(stat['mean_ticks'] for _, stat in present), max_ticks=maximum,
                round_mean_ticks=distribution([stat['mean_ticks'] for _, stat in present]),
                round_max_ticks=distribution([stat['max_ticks'] for _, stat in present]),
                slowest=[[round_no, format(sum(1 << c for c in stat['slowest_cores']), '016x')]
                         for round_no, stat in present if stat['max_ticks'] == maximum])


def csv_file(path, header, rows):
    with path.open('x', newline='') as f:
        writer = csv.writer(f); writer.writerow(header); writer.writerows(rows)


def compact_slowest(samples):
    """Group only identical tied-core sets; retain exactly which rounds tied."""
    by_core_mask = {}
    for round_no, core_mask in samples:
        by_core_mask[core_mask] = by_core_mask.get(core_mask, 0) | (1 << (round_no - 1))
    return [[format(round_mask, '03x'), core_mask] for core_mask, round_mask in sorted(by_core_mask.items())]


def main():
    # Filesystem and saved receipts only. Do not import gates, initialize ACL or query devices.
    require(len(sys.argv) == 2 and len(sys.argv[1]) == 64, 'Expected extraction source SHA')
    integrity()
    require(sha(ROOT / 'build-summary.json') == BUILD_SHA, 'Build changed')
    require(sha(ROOT / 'capture-launch/summary.json') == PARENT_SHA
            and sha(ROOT / 'capture/summary.json') == WORKER_SHA, 'Capture receipts changed')
    build = json.loads((ROOT / 'build-summary.json').read_text())
    require(build['experiment_revision'] == REVISION and build['status'] == 'PASS', 'Build identity')
    for name, digest in build['artifacts'].items():
        require(sha(ROOT / name) == digest, 'Compiled/source artifact changed: ' + name)
    for name, digest in json.loads((ROOT / 'payload-manifest.json').read_text())['files'].items():
        require(sha(ROOT / name) == digest, 'Original input changed: ' + name)
    parent = json.loads((ROOT / 'capture-launch/summary.json').read_text())
    require(parent['status'] == 'PASS' and parent['namespace_exit'] == 0 and not parent['timed_out']
            and not parent['cleanup_actions'] and not parent['own_residual_children']
            and parent['original_configuration_preserved'] and len(parent['groups']) == 10, 'Capture lifecycle')
    require(not OUT.exists(), 'Saved export exists; read it instead of overwriting')
    OUT.mkdir()
    sys.path.insert(0, str(ROOT / 'source04/tools/tilelang_runtime_experiment'))
    from analyze import analyze_rank
    from tilelang_dispatch.runtime_profile import STAGES
    sys.path.insert(0, str(ROOT / 'source04/tools/profiling/python'))
    from tilexr_profiling.records import load_record_files
    captures = []; comparisons = []; stage_rows = []; slow_rows = []; us_rows = []
    workload_rows = []; source_rows = []; trace = []; capture_ids = set()
    for group in parent['groups']:
        index = group['index']; round_no = index - 2
        directory = ROOT / 'capture' / ('g' + str(index).zfill(3))
        require(group['status'] == 'PASS' and sha(directory / 'summary.json') == group['summary_sha256'], 'Group changed')
        accepted = json.loads((directory / 'summary.json').read_text())
        require(accepted['status'] == 'PASS', 'Group not accepted')
        for rank in range(2):
            rank_dir = directory / ('rank' + str(rank))
            row = analyze_rank(rank_dir, directory / ('prof-rank' + str(rank)), rank)
            original = json.loads((directory / ('analysis-rank' + str(rank) + '.json')).read_text())
            normalize = lambda value: json.loads(json.dumps(value))
            require(normalize(row) == original, 'Reanalysis differs from saved accepted analysis')
            require(row['summary']['allreduce_values_checked'] == 22 and row['summary']['flags_cleared'], 'Rank acceptance')
            records = load_record_files([rank_dir / 'dispatch.records'])[0]
            require(str(records.capture_id) not in capture_ids and len(records.streams) == 64, 'Capture identity/count')
            capture_ids.add(str(records.capture_id))
            lanes = json.loads((rank_dir / 'lanes.json').read_text())
            lanes_by_core = {lane['core']: lane for lane in lanes}
            require({s.id for s in records.streams} == {rank << 16 | c for c in range(64)}, 'SDK stream identity')
            for stream in records.streams:
                core = stream.id & 0xffff; lane = lanes_by_core[core]
                require(stream.complete and not stream.overflow and stream.dropped_count == 0, 'SDK stream incomplete')
                require([v.event_id for v in stream.records] == lane['event_ids']
                        and [v.timestamp for v in stream.records] == lane['timestamps'], 'SDK parse differs from Host snapshot')
                pid = (round_no - 1) * 128 + rank * 64 + core + 1
                trace.append(dict(ph='M', name='process_name', pid=pid, tid=0,
                                  args={'name': 'round%d/rank%d/core%d independent t0' % (round_no, rank, core)}))
                values = dict(zip(lane['event_ids'], lane['timestamps'])); origin = values[1]
                for begin, (name, end) in STAGES.items():
                    if begin in values:
                        trace.append(dict(ph='X', name=name, cat='local-core-spec-1GHz', pid=pid, tid=0,
                                          ts=(values[begin] - origin) / 1000, dur=(values[end] - values[begin]) / 1000,
                                          args=dict(capture_id=str(records.capture_id), rank=rank, core=core,
                                                    round=round_no, raw_start=values[begin], raw_end=values[end],
                                                    clock='NOT_VERIFIED; lane origin independent; specification conversion only')))
            comparisons.append([round_no, rank, 'PASS'])
            source_rows.append([round_no, rank, str(records.capture_id), sha(rank_dir / 'dispatch.records'),
                                sha(directory / ('analysis-rank' + str(rank) + '.json')), row['sources']])
            for metric, stat in row['statistics'].items():
                stage_rows.append([round_no, rank, metric, stat['participants'], stat['mean_ticks'], stat['max_ticks'],
                                   json.dumps(stat['slowest_cores'], separators=(',', ':'))])
                if stat['participants']:
                    slow_rows.append([round_no, rank, metric, stat['max_ticks'], stat['max_ticks'] / 1000,
                                      json.dumps(stat['slowest_cores'], separators=(',', ':'))])
            us_rows.append([round_no, rank, *[row['summary'][metric] for metric in FOUR_US]])
            for core, workload in row['workload'].items():
                workload_rows.append([round_no, rank, core, *[workload[key] for key in
                    ('error', 'count_polls', 'gate_polls', 'payload_polls', 'route_ids_read',
                     'sent_records', 'received_records', 'sent_wire_bytes', 'received_wire_bytes')]])
            captures.append((round_no, rank, row))
    require(len(captures) == len(capture_ids) == 20, 'Twenty rank captures required')
    all_stats = {}; main_rows = []; rank_us = []; workload = []
    for rank in range(2):
        rows = [(round_no, row) for round_no, r, row in captures if r == rank]
        all_stats[str(rank)] = {metric: aggregate(rows, metric) for metric in rows[0][1]['statistics']}
        for metric in MAIN_METRICS:
            item = all_stats[str(rank)][metric]
            main_rows.append([rank, metric, item['participants'], item['mean_ticks'], item['max_ticks'], compact_slowest(item['slowest'])])
        rank_us.append(dict(rank=rank, us={metric: distribution([row['summary'][metric] for _, row in rows]) for metric in FOUR_US}))
        work_keys = ('count_polls', 'gate_polls', 'payload_polls', 'route_ids_read', 'sent_records',
                     'received_records', 'sent_wire_bytes', 'received_wire_bytes')
        cohorts = {'receiver': range(4), 'non_receiver': range(4, 64)}
        for cohort, cores in cohorts.items():
            totals = {key: [sum(row['workload'][core][key] for core in cores) for _, row in rows] for key in work_keys}
            workload.append(dict(rank=rank, cohort=cohort, cores=list(cores),
                                 per_round={key: distribution(values) for key, values in totals.items()}))
    round_max = []
    for round_no in range(1, 11):
        pair = [(r, row) for n, r, row in captures if n == round_no]
        require(len(pair) == 2, 'Missing paired rank')
        maxima = [max(row['summary'][metric] for _, row in pair) for metric in FOUR_US]
        ties = [[r for r, row in pair if row['summary'][metric] == value] for metric, value in zip(FOUR_US, maxima)]
        round_max.append([round_no, *maxima, ties])
    rank_max_us = {metric: distribution([row[i + 1] for row in round_max]) for i, metric in enumerate(FOUR_US)}
    batch_sizes = {row['summary']['actual_batch_capacity'] for _, _, row in captures}
    require(len(batch_sizes) == 1, 'Batch capacity differs')
    batch_capacity = batch_sizes.pop()
    derived_batches = sum(math.ceil(128 / batch_capacity) for _ in range(4))
    csv_file(OUT / 'four_us.csv', ['round', 'rank', *FOUR_US], us_rows)
    csv_file(OUT / 'per_round_stages.csv', ['round', 'rank', 'metric', 'participants', 'mean_ticks', 'max_ticks', 'slowest_cores_json'], stage_rows)
    csv_file(OUT / 'per_round_slowest.csv', ['round', 'rank', 'metric', 'max_ticks', 'max_us_spec_1GHz', 'cores_json'], slow_rows)
    csv_file(OUT / 'per_core_workload.csv', ['round', 'rank', 'core', 'error', 'count_polls', 'gate_polls', 'payload_polls',
             'route_ids_read', 'sent_records', 'received_records', 'sent_wire_bytes', 'received_wire_bytes'], workload_rows)
    save(OUT / 'capture-sources.json', source_rows)
    save(OUT / 'all-statistics.json', all_stats)
    save(OUT / 'workload-summary.json', workload)
    save(OUT / 'reanalysis-checks.json', comparisons)
    save(OUT / 'trace-local-spec-1GHz.json', dict(traceEvents=trace,
         clock='NOT_VERIFIED', scope='All lanes independently rebased to zero; no cross-core/rank ordering',
         units='us from ticks/1000 per 950DT 1GHz specification; not measured calibration'))
    save(OUT / 'numeric.json', dict(captures=[[n, r, row] for n, r, row in captures],
         aggregated=all_stats, workload=workload, round_rank_max_four_us=round_max))
    # A reading copy only; the exact compiled source remains in exports04 unchanged.
    source = (ROOT / 'exports04/profile/dispatch.profile.cpp').read_text()
    annotations = []
    profile_map = json.loads((ROOT / 'exports04/profile/runtime-profile.json').read_text())
    for item in profile_map['event_slots']:
        call = 'recorder.RecordMarker<%d, %d>();' % (item['slot'], item['id'])
        require(source.count(call) == 1, 'Profile annotation boundary changed')
        comment = '// [阅读注释] ID=%d，%s，%s；仅执行本分支的核记录，循环外包络。\n    ' % (item['id'], item['stage'], item['edge'])
        source = source.replace(call, comment + call)
        annotations.append(comment)
    restored = source
    for comment in annotations:
        restored = restored.replace(comment, '')
    require(restored == (ROOT / 'exports04/profile/dispatch.profile.cpp').read_text(), 'Reading copy changed code')
    (OUT / 'dispatch.profile.reading.cpp').write_text(source)
    (OUT / '交付说明.md').write_text('''# 107 ABI3 实机证据包\n\n场景：P2/E4/每rankT256/H256/K2/BF16/balanced，物理0/1各64 AIV，前置AR SUM2。64ed基线加dce8baa3目标兼容修复；3fresh raw正确性、10fresh profile采集，每组20+1。\n\n阅读入口：source04/examples/tilelang_dispatch/kernel.py 为带中文注释的TL实现；exports04/raw/dispatch.raw.cpp为后端原样导出；exports04/device为104B运行适配；exports04/profile为实际112B/24槽打点源码和mapping。validated-export01/dispatch.profile.reading.cpp仅增加阅读注释，未重新构建，实际编译源保留原样。source04/tools/tilelang_runtime_experiment为Host/golden/解析入口。\n\ninstall04保留Host及raw/profile已链接设备ELF，build04保留rel.o和命令；core-install为ABI0库。设备.o不可单独运行，依赖aarch64/CANN9.1及已验证私有L0/Config4；不复制系统CANN或凭据。source04及deps/backend/src/tl_templates保留SDK/模板依赖和许可证。源码ZIP与runner-build04修复包说明来源，原编译失败日志保留。\n\nvalidated-export01/four_us.csv为逐轮逐rank四类us；per_round_stages.csv为实际参与核mean/max；per_round_slowest.csv保留真实最慢核；per_core_workload.csv为工作量。⑤/F7须按接收核0–3与非接收核4–63分开；F8/F9只有4接收核。模块先同核合计再聚合，不相加不同分母均值或不同核最大值。全核和业务核口径均保留。\n\ntrace-local-spec-1GHz.json可用Perfetto打开，但每条核泳道独立归零，绝非对齐全局时序；raw ticks保留，ticks/1000只采用950DT 1GHz规格，时钟NOT_VERIFIED。msprof/ACL自带us独立。AR前段包含事件边界，不能解释为纯HCCL kernel时间；按每指标分别取慢rank，不把独立max当同一全局区间。\n\n新ABI3：计数READY清理在F7；输出drain属于F8；F9仅接收核，无独立final_fence打点，与旧TL039边界有差异。batches由实际每核128记录、单槽和实际batch capacity推导，未新增计数器。raw/profile每轮均验输入/输出位模式、assist/count/expert_nums/diag/SUM及双bank清理。\n\n完整records、Host快照、msprof CSV及重新SDK解析验证保留capture/。SHA256清单覆盖其余文件；zip CRC及逐文件SHA在本次CPU导出检查。无新增NPU或设备查询。\n''')
    files = {}
    roots = ['source04', 'exports04', 'install04', 'build04', 'core-install', 'runner-build03', 'runner-build04',
             'correctness', 'correctness-launch', 'correctness-entry01', 'capture', 'capture-launch', 'capture-entry01',
             'fixture', 'runner', 'validated-export01', 'deps/backend/src/tl_templates']
    for relative in roots:
        directory = ROOT / relative
        require(directory.is_dir(), 'Archive input missing: ' + relative)
        for path in directory.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc' and '.git' not in path.parts:
                require(path.resolve().is_relative_to(ROOT), 'Archive path outside experiment')
                files[str(path.relative_to(ROOT))] = path
    for path in ROOT.iterdir():
        if path.is_file() and path.suffix in ('.json', '.log', '.py', '.cpp', '.zip'):
            files[str(path.relative_to(ROOT))] = path
    require(len(files) < 20000 and sum(p.stat().st_size for p in files.values()) < 512 * 1024**2, 'Archive bounds')
    hashes = {name: sha(path) for name, path in sorted(files.items())}
    manifest = OUT / 'evidence-manifest.json'; save(manifest, dict(schema='tilelang_abi3_evidence_manifest.v1',
        candidate=build['candidate'], experiment_revision=REVISION, files=hashes, extraction_script_sha256=sys.argv[1]))
    files[str(manifest.relative_to(ROOT))] = manifest
    archive = ROOT / 'P2-ABI3-T256-dce8baa3-validated-evidence.zip'
    require(not archive.exists(), 'Archive already exists')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, path in sorted(files.items()):
            z.write(path, name)
    with zipfile.ZipFile(archive) as z:
        require(z.testzip() is None and set(z.namelist()) == set(files), 'ZIP CRC/members failed')
        for name, digest in hashes.items():
            require(hashlib.sha256(z.read(name)).hexdigest() == digest, 'ZIP member SHA mismatch')
        require(hashlib.sha256(z.read(str(manifest.relative_to(ROOT)))).hexdigest() == sha(manifest), 'ZIP manifest changed')
    compact = dict(schema='tilelang_general_107_delivery.v1', status='PASS', read_only_existing_evidence=True,
        npu_launch=False, device_queries=False, candidate=build['candidate'], experiment_revision=REVISION,
        target='141.61.73.107', devices=[0, 1], cores=[64, 64],
        case=dict(world_size=2, experts=4, tokens_per_rank=256, hidden=256, topk=2, dtype='BF16', routing='balanced'),
        compiled_capacity=build['compiled_capacity'], actual_batch_capacity=batch_capacity,
        successful_batches_per_rank_derived=derived_batches,
        batch_derivation='4 receiver cores, one assigned slot each, 128 actual received_records per core; ceil(128/capacity)',
        correctness_groups=3, capture_groups=10, rank_captures_reanalyzed=20, sdk_record_parses=20,
        saved_analysis_json_normalized_equal=True, original_configuration_preserved=True, clock='NOT_VERIFIED',
        units='Stage ticks retained; stage us=ticks/1000 per 950DT 1GHz specification, not measured calibration; ACL/msprof already us',
        mean_scope='Equal ten fresh rounds per rank, actual participant core denominator; no max/mean summation across cores',
        metric_columns=['rank', 'metric', 'participants', 'mean_ticks', 'max_ticks', 'slowest_round_core_masks'],
        slowest_columns=['round_mask_hex_bit0_is_round1', 'core_mask_hex'],
        metric_rows=main_rows, four_us_order=list(FOUR_US), rank_us=rank_us, rank_max_four_us=rank_max_us,
        workload_columns=['rank', 'cohort', 'cores', 'received_records_mean', 'received_wire_bytes_mean',
                          'sent_records_mean', 'sent_wire_bytes_mean', 'count_polls_mean', 'count_polls_max',
                          'gate_polls_mean', 'gate_polls_max', 'payload_rows_polled_mean', 'payload_rows_polled_max'],
        workload_rows=[[item['rank'], item['cohort'], len(item['cores']),
                        *[item['per_round'][key][edge] for key, edge in
                          [('received_records', 'mean'), ('received_wire_bytes', 'mean'),
                           ('sent_records', 'mean'), ('sent_wire_bytes', 'mean'),
                           ('count_polls', 'mean'), ('count_polls', 'max'),
                           ('gate_polls', 'mean'), ('gate_polls', 'max'),
                           ('payload_polls', 'mean'), ('payload_polls', 'max')]]] for item in workload],
        round_max_columns=['round', *FOUR_US, 'slowest_ranks_per_metric'], round_rank_max_four_us=round_max,
        pre_dispatch_scope='AllReduce plus event boundaries; not pure HCCL kernel duration',
        tails_scope='Each metric takes reported rank maximum independently; not a globally aligned interval',
        receiver_mask='000000000000000f', non_receiver_mask='fffffffffffffff0',
        boundary_note='ABI3 count READY clear in F7; output drain in F8; F9 receiver only; no separate final_fence marker',
        sources=dict(build_summary_sha256=BUILD_SHA, capture_parent_sha256=PARENT_SHA, capture_worker_sha256=WORKER_SHA,
                     manifest_path=str(manifest), manifest_sha256=sha(manifest),
                     captures_path=str(OUT / 'capture-sources.json'), captures_sha256=sha(OUT / 'capture-sources.json')),
        archive=dict(path=str(archive), bytes=archive.stat().st_size, sha256=sha(archive),
                     members=len(files), crc_and_all_member_sha_verified=True),
        files={name: dict(path=str(OUT / name), bytes=(OUT / name).stat().st_size, sha256=sha(OUT / name))
               for name in ('four_us.csv', 'per_round_stages.csv', 'per_round_slowest.csv', 'per_core_workload.csv',
                            'numeric.json', 'all-statistics.json', 'workload-summary.json', 'trace-local-spec-1GHz.json')})
    integrity()
    save(OUT / 'delivery.json', compact)
    text = json.dumps(compact, ensure_ascii=True, separators=(',', ':'))
    require(len(text.encode()) < 16000, 'Compact output too large; saved delivery exists, use read-only recovery')
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
