"""Recheck saved diagnostic data without compiling or launching another simulation."""
import hashlib
import json
from pathlib import Path
import sys

import yaml

root, revision, labels = Path(ROOT_VALUE), REV_VALUE, LABELS_VALUE
sys.path.insert(0, str(root/'source/tools/unitest'))
from controller import direct_group_schedule as schedule, memory_sst as mem

compiled = json.loads((root/'build-summary.json').read_text())
assert compiled['runtime_commit'] == revision and compiled['original_objects_unchanged']
hashes = {row['mode']: row['kernel_sha256'] for row in compiled['records']}
rows = []
for label in labels:
    output = root/'cases'/label
    summary = json.loads((output/'summary.json').read_text())
    report = json.loads((output/'result.json').read_text())
    cfg, case = report['simulator'], report['case']
    build, env, layout = mem.qualify(cfg)
    mode = summary['mode']; trace = summary['trace']
    key = 'original' if mode == 'original' else mode+'-'+trace
    expected = hashes[key]
    assert build['runtime_commit'] == revision == summary['runtime_commit']
    assert build['kernel']['sha256'] == summary['kernel_sha256'] == expected
    assert report['status'] == 'passed' and summary['validated']
    assert case['world_size'] == cfg['rank_size'] == 64 and cfg['iterations'] == 1
    assert case['algorithm'] == 'two-group' and report['run_options']['profiling']['enabled'] is False
    total = 64*4*case['elements_per_peer']
    assert total == summary['total_bytes_per_rank']
    prepare = report['phases']['prepare']
    assert prepare['block_dim'] == build['kernel']['block_dim'] == 64
    assert prepare['kernel_variant'] == build['kernel']['variant'] == ('direct-two-group' if mode == 'original' else 'direct-group-schedule-'+mode+'-'+trace)
    suite = Path(cfg['suite_output'])
    receipt = json.loads((suite/'process_receipt.json').read_text())
    assert receipt['returncode'] == summary['returncode'] == 0
    assert receipt['remaining_processes'] == summary['remaining_processes'] == []
    assert mem.digest(suite/'kernel.o') == expected
    for name, path in env['libraries'].items():
        matches = [v for p, v in receipt['observed_libraries'].items() if Path(p).name == name]
        assert len(matches) == 1 and matches[0]['path'] == path
        assert matches[0]['sha256'] == env['files'][path]
    chips = yaml.safe_load((suite/'v310_config/workload_config.yml').read_text())['workload_config']
    assert set(chips) == {'chip%d' % rank for rank in range(64)}
    ranks = report['operator_result']['ranks']; decoded = []
    assert len(ranks) == 64 and {r['rank'] for r in ranks} == set(range(64))
    for row in ranks:
        rank = row['rank']; chip = chips['chip%d' % rank]
        assert chip['block_dim'] == 64 and chip['subblock_dim'] == 1
        assert chip['kernel_file'] == str(suite/'kernel.o')
        assert chip['vec_func'] == 'TileXRAllToAllMemSim'
        assert chip['kernel_param']['cycleCount']['value'] == 1
        assert row['status'] == row['guards'] == row['padding'] == 'passed'
        assert row['checked_bytes'] == total
        golden_file = suite/'input_bin'/('rank%d' % rank)/'golden.bin'
        assert mem.digest(golden_file) == prepare['guarded_golden_sha256'][rank]
        golden = golden_file.read_bytes(); actual = (output/('rank%d-output.bin' % rank)).read_bytes()
        assert actual[:len(golden)] == golden
        assert hashlib.sha256(actual[32:32+total]).hexdigest() == row['output_sha256']
        for name, digest in row['dump_sha256'].items():
            assert mem.digest(suite/'bin'/name) == digest
        if mode != 'original':
            records = schedule.decode(actual[len(golden):], cfg['schedule'], rank, trace == 'profile')
            assert records == row['schedule_records']
            decoded.append(dict(rank=rank, status='passed', schedule_records=records))
    phase = None
    if mode != 'original':
        phase = schedule.summarize(decoded, cfg['schedule'], trace == 'profile')
        assert phase == report['schedule_profile'] == summary['schedule_profile']
    diagnostic_overlap = None
    if phase and trace == 'profile':
        metrics = []
        for rank_row in decoded:
            records = rank_row['schedule_records']
            if not all(r['clock_valid'] for r in records):
                continue
            value = {}
            for prefix, same in (('same', True), ('cross', False)):
                group = [r for r in records if r['same_group'] == same]
                other = [r for r in records if r['same_group'] != same]
                begin, end = schedule._phase_indices(mode, not same)
                value[prefix+'_ledger_other_communication_overlap'] = schedule._overlap(
                    [(r['stamps'][13],r['stamps'][28]) for r in group],
                    [(r['stamps'][begin],r['stamps'][end]) for r in other])
            metrics.append(value)
        diagnostic_overlap = {key: schedule._stats([r[key] for r in metrics]) for key in (
            'same_ledger_other_communication_overlap','cross_ledger_other_communication_overlap')}
    elapsed = report['phases']['simulation']['simulated_us']
    assert elapsed == summary['simulated_us'] and elapsed > 0
    rows.append(dict(label=label, mode=mode, bytes_per_peer=summary['bytes_per_peer'], validated=True,
        ranks=64, records=phase['records'] if phase else 0, simulated_us=elapsed,
        schedule_profile={k:v for k,v in phase.items() if k!='ranks'} if phase else None,
        diagnostic_dma_overlap=diagnostic_overlap, kernel_sha256=expected))
print(json.dumps(dict(status='audited', runtime_commit=revision, simulation_launched=False,
    case_count=len(rows), source_hashes_verified=True, deployed_kernels_verified=True,
    loaded_libraries_verified=True, launch_geometry_verified=True,
    saved_payloads_guards_padding_verified=True, phase_records_verified=True,
    all_owned_processes_clean=True, physical_clock_calibration_verified=False,
    simulator_counter_frequency_verified=False, records=rows)))
