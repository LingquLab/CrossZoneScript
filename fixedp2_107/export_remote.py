"""CPU-only fixed-P2 reanalysis; no ACL initialization, device query or relaunch."""
from pathlib import Path
import csv
import hashlib
import json
import math
import statistics
import sys
import zipfile

ROOT = Path('/root/tilelang-107-fixedp2-083ce35e-20261117-run01')
OUT = ROOT / 'validated-export01'
sys.path.insert(0, str(ROOT))
from operations import integrity, require, save, sha
from statistics_cpu import aggregate, capture_statistics, distribution

BUILD_SHA = 'a79c6075cbe9531c203d2548e919e66aa578f2a41e7ee5650d845e8711afee27'
CORRECTNESS_SHA = '4d36c20f09709e1ba80e42ce245ec3d9fa4bd7dd7f6d17822f5bf2e606b5e7d0'
PARENT_SHA = '37f73da4d4ae0ffbb4881e366cd2bc517a7b6c70f681b99d77385cc7426f1399'
REVISION = 'a4b8257a44f78849cf2d2db37cccffee9fb295e4'
FOUR_US = ('dispatch_acl_us', 'msprof_dispatch_us', 'pre_dispatch_event_us', 'segment_event_us')
WORK = ('received_records', 'received_wire_bytes', 'sent_records', 'sent_wire_bytes',
        'count_polls', 'gate_polls', 'payload_polls', 'receive_states', 'route_ids_read')
MAIN = ['stages.module.whole.all', *['modules.' + s + '.all' for s in
        ('prepare', 'count_publish', 'pack_send', 'count_prefix')],
        'modules.receive_output.receiver', 'modules.receive_output.non_receiver',
        *['functions.F' + str(i) + '.all' for i in range(7)],
        'functions.F7.receiver', 'functions.F7.non_receiver', 'functions.F8.receiver',
        'functions.F9.receiver', 'functions.F9.non_receiver',
        'stages.F9.payload_clear.receiver', 'stages.F9.final_fence.receiver',
        'stages.F9.final_fence.non_receiver']


def csv_file(name, header, rows):
    with (OUT / name).open('x', newline='') as stream:
        writer = csv.writer(stream); writer.writerow(header); writer.writerows(rows)


def compact_ties(samples):
    masks = {}
    for item in samples:
        mask = format(sum(1 << c for c in item['cores']), '016x')
        masks[mask] = masks.get(mask, 0) | (1 << (item['round'] - 1))
    return [[format(rounds, '03x'), cores] for cores, rounds in sorted(masks.items())]


def source_check(build):
    require(sha(ROOT / 'build-summary.json') == BUILD_SHA, 'Build receipt changed')
    require(build['status'] == 'PASS' and build['experiment_revision'] == REVISION
            and build['cores'] == [64, 64] and build['batch_capacity'] == 91, 'Wrong fixed-P2 build')
    for name, digest in build['compiled_artifacts'].items():
        require(sha(ROOT / name) == digest, 'Compiled artifact changed')
    require(sha(ROOT / 'payload.zip') == build['bundle_sha256'], 'Payload changed')
    with zipfile.ZipFile(ROOT / 'payload.zip') as archive:
        manifest = json.loads(archive.read('bundle-manifest.json'))
        for name, digest in manifest.items():
            if name.startswith(('source/', 'fixture/', 'generated/')) or name == 'input-identity.json':
                require(sha(ROOT / name) == digest, 'Frozen source/input changed: ' + name)
    for name, digest in json.loads((ROOT / 'runtime-manifest.json').read_text()).items():
        require(sha(ROOT / name) == digest, 'Frozen runtime support changed')


def main():
    require(PARENT_SHA != 'PENDING_CAPTURE_RECEIPT', 'Capture receipt must be pinned')
    integrity()
    build = json.loads((ROOT / 'build-summary.json').read_text()); source_check(build)
    require(sha(ROOT / 'correctness-launch/summary.json') == CORRECTNESS_SHA, 'Correctness receipt changed')
    require(sha(ROOT / 'capture-launch/summary.json') == PARENT_SHA, 'Capture receipt changed')
    parent = json.loads((ROOT / 'capture-launch/summary.json').read_text())
    require(parent['status'] == 'PASS' and parent['namespace_exit'] == 0 and not parent['timed_out']
            and not parent['cleanup_actions'] and not parent['own_residual_children']
            and parent['original_configuration_preserved'] and len(parent['groups']) == 10, 'Capture lifecycle')
    require(sha(ROOT / 'capture/summary.json') == parent['worker_summary_sha256'], 'Capture worker changed')
    require(not OUT.exists(), 'Saved export exists; audit or read it instead of replaying')
    OUT.mkdir()
    sys.path.insert(0, str(ROOT / 'source/examples'))
    from tilelang_dispatch.analyze_native import analyze
    captures = []; sources = []; us_rows = []; stage_rows = []; slow_rows = []; workload_rows = []
    trace = []; capture_ids = set(); normalize = lambda value: json.loads(json.dumps(value))
    for group in parent['groups']:
        index = group['index']; number = index - 2
        directory = ROOT / 'capture' / ('g' + str(index).zfill(3))
        require(group['status'] == 'PASS' and sha(directory / 'summary.json') == group['summary_sha256'], 'Group receipt changed')
        require(sha(directory / 'analysis.json') == group['analysis_sha256']
                and sha(directory / 'capture-evidence.json') == group['capture_evidence_sha256'], 'Accepted evidence changed')
        for name, digest in json.loads((directory / 'capture-evidence.json').read_text()).items():
            require(sha(directory / name) == digest, 'Capture evidence file changed')
        saved = json.loads((directory / 'analysis.json').read_text())
        fresh = analyze(directory / 'native', ROOT / 'fixture', ROOT / 'generated')
        require(normalize(fresh) == saved, 'SDK reanalysis differs from accepted analysis')
        require(fresh['status'] == 'PASS' and fresh['rounds'] == 1 and fresh['warmup'] == 20, 'Reanalysis acceptance')
        for rank in (0, 1):
            result = fresh['rank_results'][rank]
            require(result['rank'] == rank and result['cores'] == 64 and len(result['captures']) == 1, 'Rank capture identity')
            cap = result['captures'][0]
            require(cap['capture_id'] not in capture_ids and cap['operator_correctness'] == 'PASS'
                    and cap['capture_structure'] == 'PASS' and cap['performance_sample_accepted'], 'Capture identity/acceptance')
            capture_ids.add(cap['capture_id']); metrics = capture_statistics(cap)
            rank_root = directory / 'native/round1' / ('rank' + str(rank))
            sources.append(dict(round=number, rank=rank, capture_id=cap['capture_id'],
                                records=str((rank_root / 'round20.dispatch.records').relative_to(ROOT)),
                                records_sha256=sha(rank_root / 'round20.dispatch.records'),
                                analysis_sha256=group['analysis_sha256'], evidence_sha256=group['capture_evidence_sha256']))
            pair = fresh['pairs'][0]['ranks'][rank]
            require(pair['rank'] == rank and pair['pre_allreduce']['sum_results_checked'] == 22, 'Pre-AllReduce scope')
            timing = [pair['acl_event_us'], pair['device_task_us'],
                      pair['pre_allreduce']['pre_dispatch_event_us'], pair['pre_allreduce']['segment_event_us']]
            require(all(math.isfinite(value) and value > 0 for value in timing), 'Timing evidence')
            us_rows.append([number, rank, *timing])
            for metric, item in metrics.items():
                stage_rows.append([number, rank, metric, item['status'], item['participants'],
                                   item['mean_ticks'], item['max_ticks']])
                if item['participants']:
                    slow_rows.append([number, rank, metric, item['max_ticks'], item['max_ticks'] / 1000,
                                      json.dumps(item['slowest_cores'], separators=(',', ':'))])
            for lane in cap['lanes']:
                core = lane['core']; workload_rows.append([number, rank, core, *[lane['workload'][key] for key in WORK]])
                pid = (number - 1) * 128 + rank * 64 + core + 1
                trace.append(dict(ph='M', name='process_name', pid=pid, tid=0,
                                  args={'name': 'round%d/rank%d/core%d independent t0' % (number, rank, core)}))
                origin = lane['intervals']['module.whole']['begin_tick']
                for name, interval in lane['intervals'].items():
                    trace.append(dict(ph='X', name=name, cat='local-core-spec-1GHz', pid=pid, tid=0,
                                      ts=(interval['begin_tick'] - origin) / 1000, dur=interval['ticks'] / 1000,
                                      args=dict(round=number, rank=rank, core=core, capture_id=cap['capture_id'],
                                                raw_start=interval['begin_tick'], raw_end=interval['end_tick'], clock='NOT_VERIFIED')))
            captures.append(dict(round=number, rank=rank, metrics=metrics, capture=cap, four_us=timing))
    require(len(captures) == len(capture_ids) == 20, 'Twenty unique rank captures required')
    stats = {}; main_rows = []; rank_us = []; workload = []
    for rank in (0, 1):
        selected = [row for row in captures if row['rank'] == rank]
        require([row['round'] for row in selected] == list(range(1, 11)), 'Fresh rounds incomplete')
        stats[str(rank)] = {metric: aggregate([(row['round'], rank, row['metrics'][metric]) for row in selected])
                            for metric in selected[0]['metrics']}
        for metric in MAIN:
            item = stats[str(rank)][metric]
            main_rows.append([rank, metric, item['participants'], item['mean_ticks'], item['max_ticks'], compact_ties(item['slowest'])])
        rank_us.append(dict(rank=rank, us={metric: distribution([row['four_us'][i] for row in selected])
                                          for i, metric in enumerate(FOUR_US)}))
        for cohort, cores in (('receiver', range(4)), ('non_receiver', range(4, 64))):
            totals = {key: [sum(row['capture']['lanes'][core]['workload'][key] for core in cores)
                            for row in selected] for key in WORK}
            workload.append(dict(rank=rank, cohort=cohort, cores=len(cores),
                                 per_round={key: distribution(values) for key, values in totals.items()}))
    round_max = []
    for number in range(1, 11):
        pair = [row for row in captures if row['round'] == number]
        require(len(pair) == 2, 'Missing paired rank')
        maxima = [max(row['four_us'][i] for row in pair) for i in range(4)]
        ties = [[row['rank'] for row in pair if row['four_us'][i] == value] for i, value in enumerate(maxima)]
        round_max.append([number, *maxima, ties])
    csv_file('four_us.csv', ['round', 'rank', *FOUR_US], us_rows)
    csv_file('per_round_stages.csv', ['round', 'rank', 'metric', 'status', 'participants', 'mean_ticks', 'max_ticks'], stage_rows)
    csv_file('per_round_slowest.csv', ['round', 'rank', 'metric', 'max_ticks', 'max_us_spec_1GHz', 'cores_json'], slow_rows)
    csv_file('per_core_workload.csv', ['round', 'rank', 'core', *WORK], workload_rows)
    save(OUT / 'capture-sources.json', sources); save(OUT / 'all-statistics.json', stats)
    save(OUT / 'workload-summary.json', workload)
    save(OUT / 'numeric.json', dict(captures=captures, aggregated=stats, workload=workload, round_rank_max_four_us=round_max))
    save(OUT / 'trace-local-spec-1GHz.json', dict(traceEvents=trace, clock='NOT_VERIFIED',
         scope='All core lanes independently rebased to zero; no cross-core/rank ordering',
         units='us from ticks/1000 per 950DT 1GHz specification; not measured calibration'))
    (OUT / '交付说明.md').write_text('''# 固定2rank F4/F8优化107实机证据

P2/E4/每rankT256/H256/K2/BF16 balanced，物理0/1各64 AIV，前置AR SUM2；3fresh raw正确性及10fresh profile采集，每组20+1。

实现083ce35e，实验a4b8257a仅适配已验HCCL-first/Config4/RA attach/关闭顺序，保留固定112B Args及布局，未整替换b520的104B Host。原固定2rank812b分支不改。source/examples/tilelang_dispatch为实际编译源；generated/rank0和rank1分别为64核原始TL后端导出、适配及52槽打点。install为独立构建的Host及设备ELF，build保留rel.o/命令，core-install为ABI0通信库。无旧ELF复用。fixture中12业务文件沿用TL039 SHA，描述符按64/64固定ABI重新生成。

F4采用整批向量比较/计数及wire打包；F8采用整批READY全等和跨步assist及MTE2/MTE3硬事件A/B流水，第三批复用前等待，最终排空。实际UB191264B，batch capacity91。每接收核128记录，ceil(128/91)=2批，每rank共8个成功批次；这是工作量推导而非新增计数器。

四类us独立报告。module/F/stage先同核累计不重叠区间，再按真实参与核和相同分组聚合，十轮等权。⑤/F7/F9覆盖64核，F8仅接收核0–3；接收核与非接收核4–63分开统计，NOT_EXECUTED不是测量0。真实最慢值保留rank/core/轮次，不能相加各段max。F8最终output drain已计入F8，F9保留payload_clear/final_fence；与旧输出drain归属不同的版本须对齐⑤或F8+F9边界后比较。

raw ticks保留，ticks/1000仅950DT 1GHz规格换算，clock NOT_VERIFIED。trace每核独立归零，不表示全局时序。ACL/msprof已是us；前置区间含AR和事件边界，不等同纯HCCL kernel。按每指标分别取慢rank，不能把独立max当全局同一轮区间。

CPU重新用SDK解析20份records，验证与采集时保存analysis在JSON键规范化后完全相同。四类us、逐轮阶段、真实最慢核、工作量、完整trace和SHA清单留包；只读取已有证据，不查询设备、不启动NPU。00按107原生64/64、相同输入与计时边界比较，历史56/64单列。两仓正式分支未推送。
''')
    files = {}
    for relative in ('source', 'generated', 'fixture', 'install', 'build', 'core-install',
                     'correctness', 'correctness-launch', 'correctness-entry01',
                     'capture', 'capture-launch', 'capture-entry01', 'validated-export01',
                     'deps/backend/src/tl_templates'):
        directory = ROOT / relative; require(directory.is_dir(), 'Archive input absent: ' + relative)
        for path in directory.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and '.git' not in path.parts and path.suffix != '.pyc':
                require(path.resolve().is_relative_to(ROOT), 'Archive path escapes experiment')
                files[str(path.relative_to(ROOT))] = path
    for path in ROOT.iterdir():
        if path.is_file() and path.suffix in ('.json', '.log', '.py', '.cpp', '.zip'):
            files[str(path.relative_to(ROOT))] = path
    require(len(files) < 20000 and sum(p.stat().st_size for p in files.values()) < 1024**3, 'Archive bounds')
    hashes = {name: sha(path) for name, path in sorted(files.items())}
    manifest = OUT / 'evidence-manifest.json'
    save(manifest, dict(schema='tilelang_fixedp2_107_manifest.v1', candidate=build['candidate'],
                        experiment_revision=REVISION, files=hashes, extraction_script_sha256=sha(Path(__file__))))
    files[str(manifest.relative_to(ROOT))] = manifest
    archive = ROOT / 'P2-F4F8-T256-083ce35e-validated-evidence.zip'
    require(not archive.exists(), 'Archive already exists; inspect saved evidence')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, path in sorted(files.items()): z.write(path, name)
    with zipfile.ZipFile(archive) as z:
        require(z.testzip() is None and set(z.namelist()) == set(files), 'ZIP CRC/membership')
        for name, path in files.items():
            require(hashlib.sha256(z.read(name)).hexdigest() == sha(path), 'ZIP member SHA')
    compact = dict(schema='tilelang_fixedp2_107_delivery.v1', status='PASS', read_only_existing_evidence=True,
        npu_launch=False, device_queries=False, candidate=build['candidate'], experiment_revision=REVISION,
        target='141.61.73.107', devices=[0, 1], cores=[64, 64],
        case=dict(world_size=2, experts=4, tokens_per_rank=256, hidden=256, topk=2, dtype='BF16', routing='balanced'),
        correctness_groups=3, capture_groups=10, rank_captures_reanalyzed=20, sdk_record_parses=20,
        saved_analysis_json_normalized_equal=True, original_configuration_preserved=True,
        batch_capacity=91, successful_batches_per_rank_derived=8, ub_bytes=191264, clock='NOT_VERIFIED',
        metric_columns=['rank', 'metric', 'participants', 'mean_ticks', 'max_ticks', 'slowest_round_core_masks'],
        slowest_columns=['round_mask_hex_bit0_is_round1', 'core_mask_hex'], metric_rows=main_rows,
        four_us_order=list(FOUR_US), rank_us=rank_us,
        rank_max_four_us={metric: distribution([row[i + 1] for row in round_max]) for i, metric in enumerate(FOUR_US)},
        round_max_columns=['round', *FOUR_US, 'slowest_ranks_per_metric'], round_rank_max_four_us=round_max,
        workload_columns=['rank', 'cohort', 'cores', 'received_records_mean', 'received_wire_bytes_mean',
                          'sent_records_mean', 'sent_wire_bytes_mean', 'count_polls_mean', 'count_polls_max',
                          'gate_polls_mean', 'gate_polls_max', 'payload_polls_mean', 'payload_polls_max'],
        workload_rows=[[row['rank'], row['cohort'], row['cores'], *[row['per_round'][key][edge] for key, edge in
                       [('received_records', 'mean'), ('received_wire_bytes', 'mean'), ('sent_records', 'mean'),
                        ('sent_wire_bytes', 'mean'), ('count_polls', 'mean'), ('count_polls', 'max'),
                        ('gate_polls', 'mean'), ('gate_polls', 'max'), ('payload_polls', 'mean'), ('payload_polls', 'max')]]]
                       for row in workload],
        units='Stage ticks retained; stage us=ticks/1000 per 950DT 1GHz specification, not measured calibration; ACL/msprof already us',
        mean_scope='Equal ten rounds per rank; actual participants; same-core interval composition',
        pre_dispatch_scope='AllReduce plus event boundaries; not pure HCCL kernel duration',
        tails_scope='Each metric takes rank maximum independently; not a globally aligned interval',
        boundary_note='F8 includes output drain; F9 has payload_clear/final_fence; F7/F9/receive_output 64 cores, F8 4',
        sources=dict(build_summary_sha256=BUILD_SHA, correctness_parent_sha256=CORRECTNESS_SHA,
                     capture_parent_sha256=PARENT_SHA, capture_worker_sha256=parent['worker_summary_sha256'],
                     manifest_path=str(manifest), manifest_sha256=sha(manifest),
                     captures_path=str(OUT / 'capture-sources.json'), captures_sha256=sha(OUT / 'capture-sources.json')),
        archive=dict(path=str(archive), bytes=archive.stat().st_size, sha256=sha(archive), members=len(files), crc_and_all_member_sha_verified=True),
        files={name: dict(path=str(OUT / name), bytes=(OUT / name).stat().st_size, sha256=sha(OUT / name))
               for name in ('four_us.csv', 'per_round_stages.csv', 'per_round_slowest.csv', 'per_core_workload.csv',
                            'numeric.json', 'all-statistics.json', 'workload-summary.json', 'trace-local-spec-1GHz.json')})
    integrity(); source_check(build)
    save(OUT / 'delivery.json', compact)
    text = json.dumps(compact, ensure_ascii=True, separators=(',', ':'))
    require(len(text.encode()) < 15000, 'Output oversized; read saved delivery compactly without replay')
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
