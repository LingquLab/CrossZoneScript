"""Check pinned sources, deployed objects, saved outputs and all AIV records.
仅返回逐例摘要；完整数据和4096条记录保留在容器内。
"""
import hashlib
import json
from pathlib import Path
import sys

import yaml

root, revision, labels = Path(ROOT_VALUE), REV_VALUE, LABELS_VALUE
sys.path.insert(0, str(root/'source/tools/unitest'))
from controller import direct_group_clock as clock, memory_sst as mem

rows = []
for label in labels:
    output = root/'cases'/label
    summary = json.loads((output/'summary.json').read_text())
    report = json.loads((output/'result.json').read_text())
    cfg, case = report['simulator'], report['case']
    build, env, layout = mem.qualify(cfg)
    assert build['runtime_commit'] == revision == summary['runtime_commit']
    expected_hash = ('7b387948da24eb3e94bc6d7d7235f5aad1a03e3a35081662c40d0aef9d5568a4'
                     if summary['variant'] == 'clock' else
                     '485359c0ef685c59e73ca266546f380e8ab2dcb70c00af114cd98be911ce453a')
    assert build['kernel']['sha256'] == summary['kernel_sha256'] == expected_hash
    assert report['status'] == 'passed' and summary['validated']
    assert case['world_size'] == summary['checked_ranks'] == cfg['rank_size'] == 64
    assert case['algorithm'] == 'two-group' and cfg['iterations'] == 1
    assert report['run_options']['profiling']['enabled'] is False
    total = 64 * 4 * case['elements_per_peer']
    assert total == summary['total_bytes_per_rank']
    prepare = report['phases']['prepare']
    assert prepare['block_dim'] == summary['block_dim'] == build['kernel']['block_dim'] == 64
    assert prepare['kernel_variant'] == build['kernel']['variant']
    suite = Path(cfg['suite_output'])
    receipt = json.loads((suite/'process_receipt.json').read_text())
    assert receipt['returncode'] == summary['returncode'] == 0
    assert receipt['remaining_processes'] == summary['remaining_processes'] == []
    assert mem.digest(suite/'kernel.o') == expected_hash
    for name, expected_path in env['libraries'].items():
        matches = [v for p, v in receipt['observed_libraries'].items() if Path(p).name == name]
        assert len(matches) == 1 and matches[0]['path'] == expected_path
        assert matches[0]['sha256'] == env['files'][expected_path]
    chips = yaml.safe_load((suite/'v310_config/workload_config.yml').read_text())['workload_config']
    assert set(chips) == {'chip%d' % rank for rank in range(64)}
    ranks = report['operator_result']['ranks']
    assert len(ranks) == 64 and {r['rank'] for r in ranks} == set(range(64))
    decoded = []
    for row in ranks:
        rank = row['rank']
        chip = chips['chip%d' % rank]
        assert chip['block_dim'] == 64 and chip['subblock_dim'] == 1
        assert chip['kernel_file'] == str(suite/'kernel.o')
        assert chip['vec_func'] == 'TileXRAllToAllMemSim'
        assert chip['kernel_param']['cycleCount']['value'] == 1
        assert row['status'] == row['guards'] == row['padding'] == 'passed'
        assert row['checked_bytes'] == total
        golden_file = suite/'input_bin'/('rank%d' % rank)/'golden.bin'
        assert mem.digest(golden_file) == prepare['guarded_golden_sha256'][rank]
        golden = golden_file.read_bytes()
        actual = (output/('rank%d-output.bin' % rank)).read_bytes()
        assert actual[:len(golden)] == golden
        assert hashlib.sha256(actual[32:32+total]).hexdigest() == row['output_sha256']
        for name, expected in row['dump_sha256'].items():
            assert mem.digest(suite/'bin'/name) == expected
        if summary['variant'] == 'clock':
            records = clock.decode(actual[len(golden):], cfg['clock'], rank)
            assert records == row['clock_records']
            decoded.append(dict(clock_records=records))
        else:
            assert len(actual) == len(golden) and 'clock' not in cfg
    phase = clock.summarize(decoded, cfg['clock']) if decoded else None
    assert phase == report.get('clock_phase') == summary['clock_phase']
    simulated_us = report['phases']['simulation']['simulated_us']
    assert simulated_us == summary['simulated_us'] and simulated_us > 0
    rows.append(dict(label=label, variant=summary['variant'], size=summary['size'],
                     validated=True, ranks=64, block_dim=64, simulated_us=simulated_us,
                     clock_phase=phase, kernel_sha256=expected_hash))

print(json.dumps(dict(status='audited', runtime_commit=revision, case_count=len(rows),
    source_hashes_verified=True, deployed_kernels_verified=True, loaded_libraries_verified=True,
    launch_geometry_verified=True, saved_payloads_guards_padding_verified=True,
    clock_records_verified=True, all_owned_processes_clean=True,
    hardware_calibration_verified=False, simulator_counter_frequency_verified=False,
    records=rows)))
