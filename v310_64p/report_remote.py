from pathlib import Path
import csv
import hashlib
import json
import shutil
import subprocess
import time

case = Path('/opt/sim/simulator/Testcases/dispatch_memory_64p_top6_profile_bridge_20261119_run07_corecount')
package = Path('/work/src/dispatch_memory_64p_handoff')
started = time.monotonic()
evidence = {'case': str(case), 'phase_wall_seconds': {}}
session_dir = case / 'analysis-01'


def report_script(manifest):
    variant = manifest.get('simulation_variant')
    if variant is None:
        return package / 'profile/report.py'
    assert variant['schedule'] == 'SINGLE_ROUND' and variant['send_groups'] == 1
    analysis_package = session_dir / 'analysis-package'
    shutil.copytree(package / 'profile', analysis_package / 'profile')
    shutil.copytree(package / 'generator', analysis_package / 'generator')
    script = analysis_package / 'profile/report.py'
    text = script.read_text()
    for old, new in (
        ('groups != 2 or blocks != 30', 'groups != 1 or blocks != 30'),
        ("name='双组同步后'", "name='单轮发送结束'"),
    ):
        assert text.count(old) == 1
        text = text.replace(old, new)
    script.write_text(text)
    evidence['simulation_variant'] = variant
    return script


def run(name, argv, timeout):
    phase_start = time.monotonic()
    try:
        with (session_dir / (name + '.log')).open('xb') as log:
            proc = subprocess.run(argv, cwd=case, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        evidence[name + '_exit_code'] = proc.returncode
        if proc.returncode:
            last = (session_dir / (name + '.log')).read_text(errors='replace').splitlines()[-2:]
            evidence[name + '_error_excerpt'] = [x[:220] for x in last]
        return proc.returncode
    finally:
        evidence['phase_wall_seconds'][name] = round(time.monotonic() - phase_start, 3)


created = False
try:
    receipt = json.loads((case / 'bridge-run-receipt.json').read_text())
    assert receipt['status'] == 'SST_COMPLETE_PENDING_BUSINESS_AND_PROFILE_VALIDATION'
    generator_exit = receipt.get('output-generator_exit_code', receipt.get('output_generator_exit_code'))
    assert receipt['run_exit_code'] == 0 and generator_exit == 0
    manifest = json.loads((case / 'manifest.json').read_text())
    regions = [(row['rank'], Path(row['path']).stem, row['gm_address'], row['size_bytes'])
               for row in manifest['files'] if row['role'] == 'expected']
    regions.extend((rank['rank'], name, region['addr'], region['readback_bytes'])
                   for rank in manifest['rank_layouts'] for name, region in rank['profile_outputs'].items())
    assert len(regions) == 384
    indexed = []
    missing = []
    for rank, name, address, size in regions:
        source = case / 'bin' / f'david_{rank}_{name}.bin'
        if not source.is_file() or source.stat().st_size != size:
            missing.append([rank, name, source.stat().st_size if source.is_file() else None, size])
        else:
            indexed.append((rank, name, hex(address), str(source), 0))
    evidence['merged_region_count'] = len(indexed)
    evidence['missing_regions'] = len(missing)
    if missing:
        evidence['missing_examples'] = missing[:10]
        evidence['status'] = 'MERGED_OUTPUTS_INCOMPLETE'
    else:
        session_dir.mkdir()
        created = True
        index = session_dir / 'output-index.csv'
        with index.open('x', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['rank', 'region', 'gm_address', 'file', 'offset'])
            writer.writerows(indexed)
        normalized = session_dir / 'normalized'
        assert run('normalize', ['python3', '-B', str(package / 'profile/normalize_outputs.py'),
                                 '--testcase', str(case), '--index', str(index),
                                 '--out', str(normalized)], 160) == 0
        comparator = package / 'generator/compare_outputs.py'
        assert run('compare', ['python3', '-B', str(comparator), '--testcase', str(case),
                               '--actual', str(normalized / 'actual')], 160) == 0
        evidence['business_validation'] = json.loads((session_dir / 'compare.log').read_text())
        assert evidence['business_validation']['status'] == 'PASS' and evidence['business_validation']['checked_files'] == 256
        report = session_dir / 'report'
        assert run('report', ['python3', '-B', str(report_script(manifest)),
                              '--testcase', str(case), '--run-log', str(case / 'run.log'),
                              '--actual', str(normalized / 'actual'), '--records', str(normalized / 'records'),
                              '--out', str(report)], 240) == 0
        summary = json.loads((report / 'summary.json').read_text())
        assert summary['lanes'] == 4096 and summary['rank_count'] == 64 and len(summary['modules']) == 5
        evidence['summary'] = {field: summary[field] for field in (
            'rank_count', 'lanes', 'sst_simulated_time_us', 'clock', 'modules', 'records_manifest_sha256')}
        for row in evidence['summary']['modules']:
            row['slowest_count'] = len(row['slowest'])
            row['slowest'] = row['slowest'][:3]
        evidence['report_directory'] = str(report)
        evidence['files'] = {}
        for path in sorted(report.iterdir()):
            if path.is_file():
                checksum = hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        checksum.update(block)
                evidence['files'][path.name] = {'bytes': path.stat().st_size, 'sha256': checksum.hexdigest()}
        evidence['status'] = 'PASS'
except (AssertionError, OSError, KeyError, ValueError, subprocess.TimeoutExpired) as error:
    evidence['status'] = 'ANALYSIS_STOPPED'
    evidence['reason'] = type(error).__name__
evidence['total_wall_seconds'] = round(time.monotonic() - started, 3)
if created:
    (session_dir / 'analysis-receipt.json').write_text(json.dumps(evidence, indent=2) + '\n')
print(json.dumps(evidence, ensure_ascii=True, separators=(',', ':')))
