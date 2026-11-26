"""Recover numerics and recorded profile sectors from a completed SST case."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile

FIELDS = ('gather', 'gather_ready', 'partial', 'ready', 'local_ready', 'returned', 'completed', 'output', 'status')


def recover_profile(case, step_ends, chunks):
    run = json.loads((case / 'run.json').read_text())
    phase = json.loads((case / 'unified-result.json').read_text())
    assert run['returncode'] == 0 and run['guards']['count'] == 256
    assert run['kernel_sha256'] == '45982fb6c494379d20429d4801b66a51cd98c65afed80790d0b458744b8f3899'
    assert 'Missing dump bytes:' in run.get('error', '') and '_profile.bin' in run['error']
    original = json.loads((case / 'export-layout.json').read_text())
    layout = json.loads(json.dumps(original))
    for rank in layout['ranks']:
        del rank['exports']['profile']
    with tempfile.TemporaryDirectory(prefix='profile-recover-', dir=case) as directory:
        temp = Path(directory)
        (temp / 'numeric-layout.json').write_text(json.dumps(layout))
        exporter = (case / 'adapter/exports.py').read_text()
        # Use the same strict numeric dump decoder and allow precisely the nine numeric regions.
        import re
        exporter, allowed = re.subn(r'if name not in \([^\n]+\):', f'if name not in {FIELDS!r}:', exporter)
        exporter, required = re.subn(r'if set\(row\["exports"\]\) != \{[^\n]+\}:',
                                      f'if set(row["exports"]) != {set(FIELDS)!r}:', exporter)
        assert allowed == required == 1
        (temp / 'exports.py').write_text(exporter)
        env = json.loads((case / 'sst-environment.json').read_text())
        work_case = Path('/work/case')
        work_temp = work_case / temp.name
        linked = ('docker', 'run', '--rm', '--runtime=runc', '--network=none', '--cap-drop=ALL',
                  '--security-opt=no-new-privileges', '--cpus=4', '--memory=16g',
                  '--mount', f'type=bind,src={case},dst={work_case}', IMAGE)
        export = subprocess.run([*linked, 'python3', str(work_temp / 'exports.py'),
                                 '--layout', str(work_temp / 'numeric-layout.json'), '--dump-dir', str(work_case / 'bin'),
                                 '--output', str(work_temp / 'actual'), '--decoder-library', env['decoder_library']],
                                capture_output=True, timeout=420, check=False)
        if export.returncode:
            return dict(status='numeric_export_failed', error=export.stderr.decode(errors='replace').splitlines()[-1:])
        checker = 'source/tools/unitest/simulator/experiments/inter_server_send/fixture.py'
        check = subprocess.run([sys.executable, str(case / checker), 'check', '--output', str(case / 'fixture-data'),
                                '--actual', str(temp / 'actual')], capture_output=True, timeout=180)
        parsed = next((json.loads(line) for line in check.stdout.splitlines() if line.startswith(b'{"ranks":')), {})
        if check.returncode:
            return dict(status='numeric_check_failed', checked=parsed)
        assert parsed['ranks'] == 64 and not parsed['mismatched_ranks']
        sectors = 0
        for rank in original['ranks']:
            region = rank['exports']['profile'].copy()
            region['addr'] -= rank['sys_va_base_addr']
            profile, count = decode_sparse_profile(case / 'bin', rank['rank'], region, 48)
            (temp / 'actual' / f"rank{rank['rank']:02d}" / 'profile.bin').write_bytes(profile)
            sectors += count
        parent = str((temp / 'actual').relative_to(case))
        report = extract_pipeline_profile(case, 48, 8, 16, step_ends, chunks, parent)
        hashes = {}
        for name in ('returned', 'completed', 'output', 'status'):
            digest = hashlib.sha256()
            for rank in range(64):
                digest.update((temp / 'actual' / f'rank{rank:02d}' / (name + '.bin')).read_bytes())
            hashes[name] = digest.hexdigest()
        result = dict(phase, status='sst_and_checker_passed', checked=parsed, exports_sha256=hashes,
                      profile=report, observed_profile_sectors=sectors, recovered_without_sst=True,
                      original_runner_exit_code=phase['runner_exit_code'])
        (case / 'profile-recovered-result.json').write_text(json.dumps(result, separators=(',', ':')))
        return result
