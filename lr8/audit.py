"""Recheck the case receipt and phase intervals for a retained LR8 measurement."""
import argparse
import ast
import json
from pathlib import Path
import re
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--args-file', type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
label = args['label']
assert re.fullmatch('[a-z0-9-]{1,45}', label)
assert 1 <= len(args['cases']) <= 12
assert all(re.fullmatch(r'(?:8|16|32|64)p-\d+i32-\d+-(?:measurement|search|confirmation)', case)
           for case in args['cases'])
program = "import json, math, re, struct\nfrom pathlib import Path\n"
program += 'LABEL = ' + repr('tilexr-lr8-0c04d831-' + label) + '\n'
program += 'CASES = ' + repr(args['cases']) + '\n'
worker = Path(__file__).with_name('remote.py').read_text(encoding='utf-8')
boundary, = [node for node in ast.parse(worker).body
             if isinstance(node, ast.FunctionDef) and node.name == 'phase_boundaries']
program += ast.get_source_segment(worker, boundary) + '\n'
program += r'''
root, = Path('/opt').glob(LABEL + '-*')
proof = []
for name in CASES:
    case = root / name
    receipt = json.loads((case / 'execution.json').read_text())
    scenario = json.loads((case / 'scenario.json').read_text())
    provenance = json.loads((case / 'production_provenance.json').read_text())
    match = re.fullmatch(r'(8|16|32|64)p-(\d+)i32-(\d+)-(measurement|search|confirmation)', name)
    ranks, elements, setting = map(int, match.group(1, 2, 3))
    row = dict(ranks=ranks, elements_per_peer=elements, schedule=setting, sample=match[4],
               runtime_commit=provenance['runtime_commit'], binary_sha256=provenance['kernel_sha256'],
               state=receipt['state'])
    if receipt['state'] == 'simulation_outputs_verified':
        assert scenario['rank_size'] == ranks and scenario['elements_per_peer'] == elements
        assert receipt['runner_returncode'] == 0 and not receipt['remaining_processes']
        assert math.isfinite(receipt['simulated_us']) and receipt['simulated_us'] > 0
        row.update(payload_verified=True, simulated_us=receipt['simulated_us'],
                   routing=scenario['simulator']['routing_experiment']['profile'])
        if (case / 'stage-profiling').exists():
            row.update(phase_boundaries(case / 'stage-profiling', ranks))
    proof.append(row)
print(json.dumps(proof))
'''
compile(program, '<audit-remote>', 'exec')
result = subprocess.run(
    ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'IdentitiesOnly=yes',
     '-i', 'C:/Users/l00929943/.ssh/id_ed25519_server', 'root@141.61.94.30', 'python3 -'],
    input=program, text=True, capture_output=True, timeout=90)
print(json.dumps(dict(ssh_exit=result.returncode,
                      records=json.loads(result.stdout) if result.returncode == 0 else [])))
raise SystemExit(result.returncode != 0)
