"""Validate the staged CLI in an owned container and preserve all diagnostic logs."""
import json
from pathlib import Path
import subprocess
import time

root = Path('/home/glm16-09b20820')
helper = str(root / 'container_runtime.py')
gate = subprocess.run(['python3', helper, 'gate'], capture_output=True, text=True, timeout=75)
assert gate.returncode == 0
observation = json.loads(gate.stdout)
out = {'idle_before_check': observation['idle'], 'validated': False}
if observation['idle']:
    run_id = 'validate-' + str(time.time_ns())
    logs = root / 'results' / run_id
    logs.mkdir(parents=True)
    created = subprocess.run(['python3', helper, 'create', '--kind', 'validate', '--run-id', run_id],
                             capture_output=True, text=True, timeout=105)
    (logs / 'create.stderr.log').write_text(created.stderr)
    out['create_exit'] = created.returncode
    if created.returncode == 0:
        cid = json.loads(created.stdout)['id']
        try:
            p = subprocess.run(['docker', 'start', '-a', cid], capture_output=True, text=True, timeout=105)
            (logs / 'stdout.log').write_text(p.stdout)
            (logs / 'stderr.log').write_text(p.stderr)
            out['cli_exit'] = p.returncode
            out['validated'] = p.returncode == 0 and 'GLM_CLI_VALIDATED=' in p.stdout
            errors = p.stderr.splitlines()[-8:] if p.returncode else []
            out['error_excerpt'] = [x[:500] for x in errors]
        except subprocess.TimeoutExpired:
            out['timeout_seconds'] = 105
        finally:
            p = subprocess.run(['python3', helper, 'remove', '--id', cid],
                               capture_output=True, text=True, timeout=45)
            out['cleanup'] = json.loads(p.stdout) if p.returncode == 0 else {'exit': p.returncode}
    out['logs'] = str(logs)
print(json.dumps(out))
