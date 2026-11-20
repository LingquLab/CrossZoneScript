"""Diagnose existing 516B dumps, then run the fresh 512B matrix point."""
import json
from pathlib import Path
import subprocess
import sys

import run_lr8_singlepoints as singlepoints


root, report = singlepoints.ROOT, singlepoints.REPORT
workspace = json.loads((root / 'workspace.json').read_text(encoding='utf-8'))
request = json.loads((report / 'current-request.json').read_text(encoding='utf-8'))
assert request['container'] == 'tilexr-matrix-acc8c509-lr8-1'
assert request['runtime_commit'] == '85afd1fa61669a5ef800a80bec4df93745f119c6'
names = ('cluster', 'host', 'hostname', 'container', 'container_id', 'image_id',
         'sources', 'output', 'runtime_commit', 'source_lock_sha256', 'cann_root',
         'driver_root', 'cann_env', 'cann_env_sha256', 'ubus_library', 'ubus_sha256',
         'environment_report', 'sim_variant')
destination = report / 'diagnose-output-516b'
assert not destination.exists(), 'Diagnosis already exists; read its report instead of repeating'
command = [sys.executable, str(Path(workspace['checkouts']['tool']) / 'scripts/run_alltoall_sim_kernel.py'),
           'diagnose-output', '--inventory', workspace['inventory']]
for name in names:
    command += ['--' + name.replace('_', '-'), request[name]]
command += ['--suite-output', '/work/lr8-1/lr8-8p-516b', '--rank-size', '8', '--elements-per-peer', '129',
            '--threads', '8', '--timeout', '300', '--local-output', str(destination)]
with (report / 'diagnose-output-516b.log').open('wb') as log:
    completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=360)
if completed.returncode:
    raise RuntimeError('Retained output diagnosis failed; inspect saved report')
assert json.loads((destination / 'result.json').read_text(encoding='utf-8'))['state'] == 'output_diagnosis_collected'
rows = json.loads((destination / 'transport.json').read_text(encoding='utf-8'))
records = [json.loads(line[len('OUTPUT_DIAGNOSIS='):]) for line in rows[0]['stdout'].splitlines()
           if line.startswith('OUTPUT_DIAGNOSIS=')]
assert len(records) == 1
diagnosis = records[0]
summary = {'case': '/work/lr8-1/lr8-8p-516b', 'scope': 'existing dump diagnosis; no SST rerun', 'ranks': []}
for row in diagnosis['ranks']:
    rank = row['rank']
    summary['ranks'].append({'rank': rank, 'guards_ok': row['guards_ok'],
        'kernel_status_zero_on_both_dies': all(status['zero'] for status in row['status']),
        'output_mismatch_bytes': sum(peer['output']['mismatch_bytes'] for peer in row['peers']),
        'received_mismatch_bytes': sum(peer.get('received', {}).get('mismatch_bytes', 0) for peer in row['peers']),
        'peers': [{'source_rank': peer['source_rank'], 'output_mismatch_bytes': peer['output']['mismatch_bytes'],
            'first_output_bad_byte': peer['output']['first_byte'],
            'received_mismatch_bytes': peer.get('received', {}).get('mismatch_bytes'),
            'first_received_bad_byte': peer.get('received', {}).get('first_byte'),
            'received_vs_output_mismatch_bytes': peer.get('received_vs_output', {}).get('mismatch_bytes'),
            'input_offset_mod512': peer['input_offset_mod512']} for peer in row['peers']] if rank == 0 else []})
singlepoints.save('diagnosis-516b-summary.json', summary)
print(json.dumps({'diagnosis': summary}, ensure_ascii=True), flush=True)
singlepoints.main(elements_to_run=(128,), progress_file='unitest-512b-progress.json', result_file='singlepoint-512b-results.json')
